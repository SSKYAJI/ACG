#!/usr/bin/env node
import { existsSync, mkdirSync, realpathSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkWrite, type AcgConfig } from "./core/checkWrite.js";
import {
  CLAUDE_GUARD_SCRIPT_PATH,
  CLAUDE_SETTINGS_PATH,
  detectClaudeHook,
  formatClaudeOutput,
  installClaudeHook,
  parseClaudeInput,
  removeClaudeHook
} from "./platforms/claude.js";
import {
  WINDSURF_HOOKS_PATH,
  detectWindsurfHook,
  installWindsurfHook,
  parseWindsurfInput,
  removeWindsurfHook
} from "./platforms/windsurf.js";

const CONFIG_PATH = path.join(".acg", "acg.config.json");
const LOCKFILE_PATH = path.join(".acg", "agent_lock.json");

const SAMPLE_CONFIG: AcgConfig = {
  enabled: true,
  mode: "warn",
  current_task: "task_b",
  lockfile: ".acg/agent_lock.json"
};

const SAMPLE_LOCKFILE = {
  tasks: {
    task_b: {
      allowed_paths: ["src/routes/billing.tsx", "tests/billing.test.ts"],
      predicted_writes: ["src/routes/billing.tsx", "tests/billing.test.ts"],
      depends_on: []
    }
  }
};

function readJson<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, "utf8")) as T;
}

function writeJson(filePath: string, value: unknown): void {
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function configPath(cwd: string): string {
  return path.join(cwd, CONFIG_PATH);
}

function ensureInitialized(cwd: string): void {
  if (!existsSync(configPath(cwd))) {
    throw new Error("ACG is not initialized. Run `npx acg init` first.");
  }
}

export function init(cwd = process.cwd()): string {
  const lines = ["ACG init", ""];
  const acgDir = path.join(cwd, ".acg");
  if (!existsSync(acgDir)) {
    mkdirSync(acgDir, { recursive: true });
    lines.push("Created: .acg/");
  } else {
    lines.push("Already existed: .acg/");
  }

  const cfgPath = configPath(cwd);
  if (!existsSync(cfgPath)) {
    writeJson(cfgPath, SAMPLE_CONFIG);
    lines.push("Created: .acg/acg.config.json");
  } else {
    lines.push("Already existed: .acg/acg.config.json");
  }

  const lockPath = path.join(cwd, LOCKFILE_PATH);
  if (!existsSync(lockPath)) {
    writeJson(lockPath, SAMPLE_LOCKFILE);
    lines.push("Created: .acg/agent_lock.json");
  } else {
    lines.push("Already existed: .acg/agent_lock.json");
  }

  const claudeStatus = installClaudeHook(cwd);
  const windsurfStatus = installWindsurfHook(cwd);
  lines.push(`Claude Code guard: ${claudeStatus}`);
  lines.push(`Windsurf guard: ${windsurfStatus}`);
  return lines.join("\n");
}

export function status(cwd = process.cwd()): string {
  const initialized = existsSync(configPath(cwd));
  const config = initialized ? readJson<AcgConfig>(configPath(cwd)) : null;
  return [
    "ACG status",
    "",
    `Initialized: ${initialized ? "yes" : "no"}`,
    `Enabled: ${config?.enabled ? "yes" : "no"}`,
    `Mode: ${config?.mode ?? "unknown"}`,
    `Current task: ${config?.current_task ?? "unknown"}`,
    `Lockfile: ${config?.lockfile ?? "unknown"}`,
    "",
    "Guards:",
    `Claude Code: ${detectClaudeHook(cwd) ? "enabled" : "missing"}`,
    `Windsurf: ${detectWindsurfHook(cwd) ? "enabled" : "missing"}`
  ].join("\n");
}

export function disable(cwd = process.cwd()): string {
  ensureInitialized(cwd);
  const cfgPath = configPath(cwd);
  const config = readJson<AcgConfig>(cfgPath);
  if (config.enabled === false) return "ACG is already disabled. Nothing changed.";
  config.enabled = false;
  writeJson(cfgPath, config);
  return "ACG disabled. Guards remain installed.";
}

export function enable(cwd = process.cwd()): string {
  ensureInitialized(cwd);
  const cfgPath = configPath(cwd);
  const config = readJson<AcgConfig>(cfgPath);
  config.enabled = true;
  writeJson(cfgPath, config);
  const claudeStatus = installClaudeHook(cwd);
  const windsurfStatus = installWindsurfHook(cwd);
  return [`ACG enabled.`, `Claude Code guard: ${claudeStatus}`, `Windsurf guard: ${windsurfStatus}`].join("\n");
}

export function deleteAcg(cwd = process.cwd()): string {
  const lines = ["ACG delete", ""];
  const claudeRemoved = removeClaudeHook(cwd);
  const windsurfRemoved = removeWindsurfHook(cwd);
  lines.push(
    claudeRemoved.hookRemoved
      ? `Deleted ACG-managed hook from ${CLAUDE_SETTINGS_PATH}`
      : `No ACG-managed hook found in ${CLAUDE_SETTINGS_PATH}`
  );
  lines.push(
    claudeRemoved.scriptRemoved
      ? `Deleted ACG-managed hook script ${CLAUDE_GUARD_SCRIPT_PATH}`
      : `No ACG-managed hook script found at ${CLAUDE_GUARD_SCRIPT_PATH}`
  );
  lines.push(
    windsurfRemoved
      ? `Deleted ACG-managed hook from ${WINDSURF_HOOKS_PATH}`
      : `No ACG-managed hook found in ${WINDSURF_HOOKS_PATH}`
  );

  const acgDir = path.join(cwd, ".acg");
  if (existsSync(acgDir)) {
    rmSync(acgDir, { recursive: true, force: true });
    lines.push("Deleted .acg/");
  } else {
    lines.push("No .acg/ directory found");
  }
  return lines.join("\n");
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

async function guard(args: string[]): Promise<number> {
  const platformIndex = args.indexOf("--platform");
  const platform = platformIndex >= 0 ? args[platformIndex + 1] : undefined;
  if (platform !== "claude" && platform !== "windsurf") {
    console.error("Usage: acg guard --platform <claude|windsurf>");
    return 1;
  }

  const stdin = await readStdin();
  const payload = stdin.trim() ? JSON.parse(stdin) : {};
  const input = platform === "claude" ? parseClaudeInput(payload) : parseWindsurfInput(payload);
  const result = checkWrite(input);

  if (platform === "claude") {
    process.stdout.write(`${formatClaudeOutput(result)}\n`);
    return 0;
  }

  if (result.decision === "warn") {
    console.error(result.reason);
    return 0;
  }
  if (result.decision === "block") {
    console.error(result.reason);
    return 2;
  }
  return 0;
}

export async function main(argv = process.argv.slice(2)): Promise<number> {
  const command = argv[0];
  try {
    if (command === "init") console.log(init());
    else if (command === "status") console.log(status());
    else if (command === "disable") console.log(disable());
    else if (command === "enable") console.log(enable());
    else if (command === "delete") console.log(deleteAcg());
    else if (command === "guard") return await guard(argv.slice(1));
    else {
      console.error("Usage: acg <init|status|disable|enable|delete>");
      return 1;
    }
    return 0;
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}

const entrypoint = process.argv[1] ? realpathSync(process.argv[1]) : "";
if (realpathSync(fileURLToPath(import.meta.url)) === entrypoint) {
  process.exitCode = await main();
}
