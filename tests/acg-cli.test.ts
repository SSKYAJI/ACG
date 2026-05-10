import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync, existsSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { checkWrite } from "../src/core/checkWrite.js";
import { deleteAcg, disable, enable, init, status } from "../src/cli.js";
import {
  CLAUDE_GUARD_COMMAND,
  CLAUDE_GUARD_SCRIPT_PATH,
  LEGACY_CLAUDE_GUARD_COMMAND,
  formatClaudeOutput,
  parseClaudeInput
} from "../src/platforms/claude.js";
import { parseWindsurfInput } from "../src/platforms/windsurf.js";

let dirs: string[] = [];

function tempRepo(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), "acg-test-"));
  dirs.push(dir);
  return dir;
}

function readJson<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, "utf8")) as T;
}

function writeJson(filePath: string, value: unknown): void {
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
  dirs = [];
});

describe("ACG CLI commands", () => {
  it("init is idempotent and does not duplicate hooks", () => {
    const cwd = tempRepo();
    init(cwd);
    init(cwd);

    const claude = readJson<{ hooks: { PreToolUse: Array<{ hooks: unknown[] }> } }>(
      path.join(cwd, ".claude", "settings.local.json")
    );
    const windsurf = readJson<{ pre_write_code: unknown[] }>(path.join(cwd, ".windsurf", "hooks.json"));
    const scriptPath = path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH);

    expect(claude.hooks.PreToolUse).toHaveLength(1);
    expect(claude.hooks.PreToolUse[0].hooks).toHaveLength(1);
    expect(claude.hooks.PreToolUse[0].hooks[0]).toMatchObject({ command: CLAUDE_GUARD_COMMAND, acgManaged: true });
    expect(existsSync(scriptPath)).toBe(true);
    expect(readFileSync(scriptPath, "utf8")).toContain("npx acg guard --platform claude");
    expect(statSync(scriptPath).mode & 0o111).not.toBe(0);
    expect(windsurf.pre_write_code).toHaveLength(1);
  });

  it("init migrates a legacy direct Claude hook to the wrapper", () => {
    const cwd = tempRepo();
    writeJson(path.join(cwd, ".claude", "settings.local.json"), {
      hooks: {
        PreToolUse: [
          {
            matcher: "Edit|Write",
            hooks: [{ type: "command", command: LEGACY_CLAUDE_GUARD_COMMAND, acgManaged: true }]
          }
        ]
      }
    });

    init(cwd);
    const claude = readJson<{ hooks: { PreToolUse: Array<{ hooks: Array<{ command: string }> }> } }>(
      path.join(cwd, ".claude", "settings.local.json")
    );

    expect(JSON.stringify(claude)).not.toContain(LEGACY_CLAUDE_GUARD_COMMAND);
    expect(claude.hooks.PreToolUse).toHaveLength(1);
    expect(claude.hooks.PreToolUse[0].hooks[0].command).toBe(CLAUDE_GUARD_COMMAND);
  });

  it("status prints useful basics", () => {
    const cwd = tempRepo();
    init(cwd);
    const output = status(cwd);

    expect(output).toContain("Initialized: yes");
    expect(output).toContain("Enabled: yes");
    expect(output).toContain("Mode: warn");
    expect(output).toContain("Current task: task_b");
    expect(output).toContain("Claude Code: enabled");
    expect(output).toContain("Windsurf: enabled");
  });

  it("disable reports already disabled without rewriting intent", () => {
    const cwd = tempRepo();
    init(cwd);
    disable(cwd);

    expect(disable(cwd)).toBe("ACG is already disabled. Nothing changed.");
    expect(readJson<{ enabled: boolean }>(path.join(cwd, ".acg", "acg.config.json")).enabled).toBe(false);
  });

  it("enable after disable resumes enforcement and keeps guards installed", () => {
    const cwd = tempRepo();
    init(cwd);
    disable(cwd);
    const output = enable(cwd);

    expect(readJson<{ enabled: boolean }>(path.join(cwd, ".acg", "acg.config.json")).enabled).toBe(true);
    expect(output).toContain("ACG enabled.");
    expect(status(cwd)).toContain("Claude Code: enabled");
  });

  it("delete removes only ACG-managed hooks", () => {
    const cwd = tempRepo();
    init(cwd);
    const claudePath = path.join(cwd, ".claude", "settings.local.json");
    const windsurfPath = path.join(cwd, ".windsurf", "hooks.json");

    const claude = readJson<any>(claudePath);
    claude.hooks.PreToolUse.push({
      matcher: "Edit|Write",
      hooks: [{ type: "command", command: "echo keep" }]
    });
    writeJson(claudePath, claude);

    const windsurf = readJson<any>(windsurfPath);
    windsurf.pre_write_code.push({ command: "echo keep" });
    writeJson(windsurfPath, windsurf);

    const output = deleteAcg(cwd);
    const newClaude = readJson<any>(claudePath);
    const newWindsurf = readJson<any>(windsurfPath);

    expect(output).toContain("Deleted .acg/");
    expect(output).toContain(`Deleted ACG-managed hook script ${CLAUDE_GUARD_SCRIPT_PATH}`);
    expect(existsSync(path.join(cwd, ".acg"))).toBe(false);
    expect(existsSync(path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH))).toBe(false);
    expect(JSON.stringify(newClaude)).toContain("echo keep");
    expect(JSON.stringify(newClaude)).not.toContain(CLAUDE_GUARD_COMMAND);
    expect(JSON.stringify(newWindsurf)).toContain("echo keep");
    expect(JSON.stringify(newWindsurf)).not.toContain("npx acg guard --platform windsurf");
  });
});

describe("guard core", () => {
  function initializedRepo(mode: "audit" | "warn" | "block" = "warn"): string {
    const cwd = tempRepo();
    init(cwd);
    const cfgPath = path.join(cwd, ".acg", "acg.config.json");
    const config = readJson<any>(cfgPath);
    config.mode = mode;
    writeJson(cfgPath, config);
    return cwd;
  }

  it("normalizes paths and rejects traversal outside the repo", () => {
    const cwd = initializedRepo("block");
    expect(checkWrite({ platform: "claude", cwd, filePath: "./src/routes/../routes/billing.tsx", action: "write" }).decision).toBe(
      "allow"
    );
    expect(checkWrite({ platform: "claude", cwd, filePath: "../outside.ts", action: "write" }).decision).toBe("block");
  });

  it("allows an allowed file", () => {
    const cwd = initializedRepo();
    expect(checkWrite({ platform: "claude", cwd, filePath: "src/routes/billing.tsx", action: "write" }).decision).toBe(
      "allow"
    );
  });

  it("warns for an outside file in warn mode", () => {
    const cwd = initializedRepo("warn");
    const result = checkWrite({ platform: "windsurf", cwd, filePath: "src/routes/profile.tsx", action: "write" });
    expect(result.decision).toBe("warn");
    expect(result.reason).toContain("outside allowed_paths");
  });

  it("blocks for an outside file in block mode", () => {
    const cwd = initializedRepo("block");
    const result = checkWrite({ platform: "windsurf", cwd, filePath: "src/routes/profile.tsx", action: "write" });
    expect(result.decision).toBe("block");
    expect(result.reason).toContain("src/routes/profile.tsx");
  });
});

describe("platform parsing", () => {
  it("parses Claude input", () => {
    const input = parseClaudeInput({ cwd: "/repo", tool_input: { file_path: "a.ts" } });
    expect(input).toEqual({ platform: "claude", cwd: "/repo", filePath: "a.ts", action: "write" });
  });

  it("formats Claude allow as allow", () => {
    const output = JSON.parse(formatClaudeOutput({ decision: "allow" }));
    expect(output.hookSpecificOutput.permissionDecision).toBe("allow");
    expect(output.hookSpecificOutput.permissionDecisionReason).toBeUndefined();
  });

  it("formats Claude warn as ask so the warning is visible", () => {
    const output = JSON.parse(formatClaudeOutput({ decision: "warn", reason: "outside allowed_paths" }));
    expect(output.hookSpecificOutput.permissionDecision).toBe("ask");
    expect(output.hookSpecificOutput.permissionDecisionReason).toBe("outside allowed_paths");
  });

  it("formats Claude block as deny", () => {
    const output = JSON.parse(formatClaudeOutput({ decision: "block", reason: "outside allowed_paths" }));
    expect(output.hookSpecificOutput.permissionDecision).toBe("deny");
    expect(output.hookSpecificOutput.permissionDecisionReason).toBe("outside allowed_paths");
  });

  it("parses Windsurf input", () => {
    const input = parseWindsurfInput({ tool_info: { cwd: "/repo", file_path: "a.ts" } });
    expect(input).toEqual({ platform: "windsurf", cwd: "/repo", filePath: "a.ts", action: "write" });
  });
});
