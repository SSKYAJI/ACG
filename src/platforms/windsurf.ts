import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { CheckWriteInput } from "../core/checkWrite.js";

export const WINDSURF_HOOKS_PATH = path.join(".windsurf", "hooks.json");
export const WINDSURF_GUARD_COMMAND = "npx acg guard --platform windsurf";

type WindsurfHooks = {
  pre_write_code?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

function readHooks(cwd: string): WindsurfHooks {
  const hooksPath = path.join(cwd, WINDSURF_HOOKS_PATH);
  if (!existsSync(hooksPath)) return {};
  return JSON.parse(readFileSync(hooksPath, "utf8")) as WindsurfHooks;
}

function writeHooks(cwd: string, hooks: WindsurfHooks): void {
  const hooksPath = path.join(cwd, WINDSURF_HOOKS_PATH);
  mkdirSync(path.dirname(hooksPath), { recursive: true });
  writeFileSync(hooksPath, `${JSON.stringify(hooks, null, 2)}\n`, "utf8");
}

function isAcgHook(hook: Record<string, unknown>): boolean {
  return hook.acgManaged === true || hook.command === WINDSURF_GUARD_COMMAND;
}

export function detectWindsurfHook(cwd: string): boolean {
  const hooks = readHooks(cwd);
  return hooks.pre_write_code?.some((hook) => hook.command === WINDSURF_GUARD_COMMAND) ?? false;
}

export function installWindsurfHook(cwd: string): "created" | "already existed" {
  const hooks = readHooks(cwd);
  hooks.pre_write_code ??= [];
  if (hooks.pre_write_code.some((hook) => hook.command === WINDSURF_GUARD_COMMAND)) {
    return "already existed";
  }

  hooks.pre_write_code.push({
    command: WINDSURF_GUARD_COMMAND,
    acgManaged: true
  });
  writeHooks(cwd, hooks);
  return "created";
}

export function removeWindsurfHook(cwd: string): boolean {
  const hooksPath = path.join(cwd, WINDSURF_HOOKS_PATH);
  if (!existsSync(hooksPath)) return false;

  const hooks = readHooks(cwd);
  const before = hooks.pre_write_code?.length ?? 0;
  hooks.pre_write_code = (hooks.pre_write_code ?? []).filter((hook) => !isAcgHook(hook));
  const removed = hooks.pre_write_code.length !== before;
  if (hooks.pre_write_code.length === 0) delete hooks.pre_write_code;
  writeHooks(cwd, hooks);
  return removed;
}

export function parseWindsurfInput(input: unknown): CheckWriteInput {
  const payload = input as { tool_info?: { cwd?: unknown; file_path?: unknown } };
  if (typeof payload.tool_info?.file_path !== "string") {
    throw new Error("Windsurf hook input missing tool_info.file_path");
  }
  return {
    platform: "windsurf",
    cwd: typeof payload.tool_info.cwd === "string" ? payload.tool_info.cwd : process.cwd(),
    filePath: payload.tool_info.file_path,
    action: "write"
  };
}
