import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

export type AcgMode = "audit" | "warn" | "block";

export type AcgConfig = {
  enabled: boolean;
  mode: AcgMode;
  current_task: string;
  lockfile: string;
};

export type CheckWriteInput = {
  cwd: string;
  filePath: string;
  action: "write";
  platform: "claude" | "windsurf";
};

export type CheckWriteResult = {
  decision: "allow" | "warn" | "block";
  reason?: string;
  normalizedPath?: string;
  relativePath?: string;
};

type Lockfile = {
  tasks?: Record<string, { allowed_paths?: string[] }>;
};

const CONFIG_PATH = path.join(".acg", "acg.config.json");

export function loadConfig(cwd: string): AcgConfig | null {
  const configPath = path.join(cwd, CONFIG_PATH);
  if (!existsSync(configPath)) return null;
  return JSON.parse(readFileSync(configPath, "utf8")) as AcgConfig;
}

export function normalizeInsideRepo(cwd: string, candidate: string): string | null {
  const repoRoot = path.resolve(cwd);
  const absolute = path.isAbsolute(candidate)
    ? path.resolve(candidate)
    : path.resolve(repoRoot, candidate);
  const relative = path.relative(repoRoot, absolute);

  if (relative === "") return absolute;
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return absolute;
}

export function isPathAllowed(cwd: string, filePath: string, allowedPaths: string[]): boolean {
  const target = normalizeInsideRepo(cwd, filePath);
  if (!target) return false;

  return allowedPaths.some((allowedPath) => {
    const allowed = normalizeInsideRepo(cwd, allowedPath);
    if (!allowed) return false;
    const relative = path.relative(allowed, target);
    return target === allowed || (relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative));
  });
}

export function logViolation(cwd: string, entry: Record<string, unknown>): void {
  const reportsDir = path.join(cwd, ".acg", "reports");
  mkdirSync(reportsDir, { recursive: true });
  appendFileSync(
    path.join(reportsDir, "violations.jsonl"),
    `${JSON.stringify({ timestamp: new Date().toISOString(), ...entry })}\n`,
    "utf8"
  );
}

export function checkWrite(input: CheckWriteInput): CheckWriteResult {
  const cwd = path.resolve(input.cwd);
  const config = loadConfig(cwd);
  if (!config) return { decision: "allow" };
  if (config.enabled === false) return { decision: "allow" };

  const target = normalizeInsideRepo(cwd, input.filePath);
  const relativePath = target ? path.relative(cwd, target) : input.filePath;
  const lockfilePath = normalizeInsideRepo(cwd, config.lockfile);
  if (!lockfilePath || !existsSync(lockfilePath)) {
    return {
      decision: config.mode === "block" ? "block" : "warn",
      reason: `ACG lockfile not found: ${config.lockfile}`,
      normalizedPath: target ?? undefined,
      relativePath
    };
  }

  const lockfile = JSON.parse(readFileSync(lockfilePath, "utf8")) as Lockfile;
  const task = lockfile.tasks?.[config.current_task];
  const allowedPaths = task?.allowed_paths ?? [];
  const allowed = isPathAllowed(cwd, input.filePath, allowedPaths);
  if (allowed) {
    return { decision: "allow", normalizedPath: target ?? undefined, relativePath };
  }

  const reason = `ACG blocked write outside allowed_paths: ${relativePath}`;
  if (config.mode === "audit") {
    logViolation(cwd, {
      platform: input.platform,
      action: input.action,
      filePath: relativePath,
      currentTask: config.current_task,
      reason
    });
    return { decision: "allow", reason, normalizedPath: target ?? undefined, relativePath };
  }

  if (config.mode === "warn") {
    return { decision: "warn", reason, normalizedPath: target ?? undefined, relativePath };
  }

  return { decision: "block", reason, normalizedPath: target ?? undefined, relativePath };
}
