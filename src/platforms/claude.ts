import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { CheckWriteInput, CheckWriteResult } from "../core/checkWrite.js";

export const CLAUDE_SETTINGS_PATH = path.join(".claude", "settings.local.json");
export const CLAUDE_GUARD_SCRIPT_PATH = path.join(".claude", "hooks", "acg-guard.sh");
export const CLAUDE_GUARD_COMMAND = "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/acg-guard.sh";
export const LEGACY_CLAUDE_GUARD_COMMAND = "npx acg guard --platform claude";

const CLAUDE_GUARD_SCRIPT = `#!/bin/sh
# ACG-managed Claude Code PreToolUse hook.
# Reads Claude's hook JSON from stdin and returns structured PreToolUse JSON.
exec npx acg guard --platform claude
`;

type ClaudeSettings = {
  hooks?: Record<string, Array<{ matcher?: string; hooks?: Array<Record<string, unknown>> }>>;
  [key: string]: unknown;
};

export type ClaudeRemovalResult = {
  hookRemoved: boolean;
  scriptRemoved: boolean;
};

function readSettings(cwd: string): ClaudeSettings {
  const settingsPath = path.join(cwd, CLAUDE_SETTINGS_PATH);
  if (!existsSync(settingsPath)) return {};
  return JSON.parse(readFileSync(settingsPath, "utf8")) as ClaudeSettings;
}

function writeSettings(cwd: string, settings: ClaudeSettings): void {
  const settingsPath = path.join(cwd, CLAUDE_SETTINGS_PATH);
  mkdirSync(path.dirname(settingsPath), { recursive: true });
  writeFileSync(settingsPath, `${JSON.stringify(settings, null, 2)}\n`, "utf8");
}

function isAcgHook(hook: Record<string, unknown>): boolean {
  return (
    hook.acgManaged === true ||
    hook.command === CLAUDE_GUARD_COMMAND ||
    hook.command === LEGACY_CLAUDE_GUARD_COMMAND
  );
}

function ensureClaudeGuardScript(cwd: string): "created" | "already existed" {
  const scriptPath = path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH);
  if (existsSync(scriptPath) && readFileSync(scriptPath, "utf8") === CLAUDE_GUARD_SCRIPT) {
    chmodSync(scriptPath, 0o755);
    return "already existed";
  }

  mkdirSync(path.dirname(scriptPath), { recursive: true });
  writeFileSync(scriptPath, CLAUDE_GUARD_SCRIPT, { encoding: "utf8", mode: 0o755 });
  chmodSync(scriptPath, 0o755);
  return "created";
}

export function detectClaudeGuardScript(cwd: string): boolean {
  const scriptPath = path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH);
  return existsSync(scriptPath) && readFileSync(scriptPath, "utf8") === CLAUDE_GUARD_SCRIPT;
}

export function detectClaudeHook(cwd: string): boolean {
  const settings = readSettings(cwd);
  const hasHook =
    settings.hooks?.PreToolUse?.some((entry) =>
      entry.matcher === "Edit|Write" && entry.hooks?.some((hook) => hook.command === CLAUDE_GUARD_COMMAND)
    ) ?? false;
  return hasHook && detectClaudeGuardScript(cwd);
}

function removeAcgHookEntries(settings: ClaudeSettings): boolean {
  const preToolUse = settings.hooks?.PreToolUse;
  if (!preToolUse) return false;

  let removed = false;
  settings.hooks!.PreToolUse = preToolUse
    .map((entry) => {
      const hooks = entry.hooks ?? [];
      const keptHooks = hooks.filter((hook) => {
        const remove = isAcgHook(hook);
        removed ||= remove;
        return !remove;
      });
      return { ...entry, hooks: keptHooks };
    })
    .filter((entry) => (entry.hooks?.length ?? 0) > 0);

  if (settings.hooks!.PreToolUse.length === 0) delete settings.hooks!.PreToolUse;
  return removed;
}

export function installClaudeHook(cwd: string): "created" | "already existed" {
  const settings = readSettings(cwd);
  settings.hooks ??= {};
  settings.hooks.PreToolUse ??= [];

  const alreadyInstalled = detectClaudeHook(cwd);
  const scriptStatus = ensureClaudeGuardScript(cwd);
  if (alreadyInstalled) return scriptStatus === "created" ? "created" : "already existed";

  removeAcgHookEntries(settings);
  settings.hooks.PreToolUse ??= [];

  settings.hooks.PreToolUse.push({
    matcher: "Edit|Write",
    hooks: [
      {
        type: "command",
        command: CLAUDE_GUARD_COMMAND,
        acgManaged: true
      }
    ]
  });
  writeSettings(cwd, settings);
  return "created";
}

export function removeClaudeHook(cwd: string): ClaudeRemovalResult {
  const settingsPath = path.join(cwd, CLAUDE_SETTINGS_PATH);
  const result = { hookRemoved: false, scriptRemoved: false };

  if (!existsSync(settingsPath)) {
    const scriptPath = path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH);
    if (detectClaudeGuardScript(cwd)) {
      rmSync(scriptPath, { force: true });
      result.scriptRemoved = true;
    }
    return result;
  }

  const settings = readSettings(cwd);
  result.hookRemoved = removeAcgHookEntries(settings);
  writeSettings(cwd, settings);

  if (detectClaudeGuardScript(cwd)) {
    rmSync(path.join(cwd, CLAUDE_GUARD_SCRIPT_PATH), { force: true });
    result.scriptRemoved = true;
  }
  return result;
}

export function parseClaudeInput(input: unknown): CheckWriteInput {
  const payload = input as { cwd?: unknown; tool_input?: { file_path?: unknown } };
  if (typeof payload.cwd !== "string") throw new Error("Claude hook input missing cwd");
  if (typeof payload.tool_input?.file_path !== "string") {
    throw new Error("Claude hook input missing tool_input.file_path");
  }
  return {
    platform: "claude",
    cwd: payload.cwd,
    filePath: payload.tool_input.file_path,
    action: "write"
  };
}

export function formatClaudeOutput(result: CheckWriteResult): string {
  if (result.decision === "block") {
    return JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: result.reason
      }
    });
  }

  if (result.decision === "warn") {
    return JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: result.reason
      }
    });
  }

  return JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "allow"
    }
  });
}
