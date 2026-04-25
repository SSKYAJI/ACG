/**
 * ACG graph builder.
 *
 * Walks a TypeScript / JavaScript repository with ts-morph and emits the JSON
 * shape consumed by ``acg.predictor``. Output is deterministic so the demo
 * benchmark stays reproducible.
 *
 * Usage:
 *   tsx scan.ts --repo <repo-root> --out <context_graph.json>
 */

import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { Node, Project, SourceFile, SyntaxKind } from "ts-morph";

// Hotspot threshold tuned for the demo-app. Real-world repositories typically
// use 5+ importers as the cut-off; small Next.js scaffolds rarely hit that
// volume even when modules are clearly central, so 3 is a more honest signal.
const HOTSPOT_THRESHOLD = 3;
const SOURCE_GLOBS = ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"];
const IGNORE_GLOBS = [
  "**/node_modules/**",
  "**/.next/**",
  "**/dist/**",
  "**/.turbo/**",
  "**/build/**",
  "**/.git/**",
  "**/coverage/**",
];

interface CliArgs {
  repo: string;
  out: string;
}

function parseArgs(argv: string[]): CliArgs {
  const args: Partial<CliArgs> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === "--repo" && value) {
      args.repo = value;
      i += 1;
    } else if (flag === "--out" && value) {
      args.out = value;
      i += 1;
    }
  }
  if (!args.repo || !args.out) {
    throw new Error(
      "usage: tsx scan.ts --repo <repo-root> --out <context_graph.json>",
    );
  }
  return args as CliArgs;
}

interface FileNode {
  path: string;
  imports: string[];
  exports: string[];
  symbols: string[];
  default_export: string | null;
  is_hotspot: boolean;
  imported_by_count: number;
}

function relativePath(repoRoot: string, absolute: string): string {
  return relative(repoRoot, absolute).split("\\").join("/");
}

interface AliasMapping {
  prefix: string; // e.g. "~/"
  target: string; // absolute path the prefix expands to
}

// String-aware JSONC stripper: removes line and block comments while leaving
// identical sequences inside JSON string literals untouched. tsconfig "paths"
// values frequently contain slash-star sequences (e.g. ``"~/*"``) that a naive
// global regex would treat as a comment opener.
function stripJsonc(text: string): string {
  let out = "";
  let inString = false;
  let escapeNext = false;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (escapeNext) escapeNext = false;
      else if (ch === "\\") escapeNext = true;
      else if (ch === '"') inString = false;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inString = true;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
      continue;
    }
    if (ch === "/" && text[i + 1] === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

/**
 * Parse ``compilerOptions.paths`` from a project's ``tsconfig.json`` so the
 * import-graph counter can resolve aliased imports (T3's ``~/*`` form, the
 * popular ``@/*`` form, etc.). Anything more exotic falls through and is
 * treated as an external module.
 */
function loadAliases(repoAbs: string): AliasMapping[] {
  const tsconfigPath = resolve(repoAbs, "tsconfig.json");
  if (!existsSync(tsconfigPath)) return [];
  let parsed: { compilerOptions?: { baseUrl?: string; paths?: Record<string, string[]> } };
  try {
    const raw = stripJsonc(readFileSync(tsconfigPath, "utf8")).replace(
      /,(\s*[\]}])/g,
      "$1",
    );
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  const baseUrl = parsed.compilerOptions?.baseUrl ?? ".";
  const baseAbs = resolve(repoAbs, baseUrl);
  const paths = parsed.compilerOptions?.paths ?? {};
  const out: AliasMapping[] = [];
  for (const [aliasGlob, targets] of Object.entries(paths)) {
    if (!aliasGlob.endsWith("/*") || !targets?.length) continue;
    const targetGlob = targets[0];
    if (!targetGlob.endsWith("/*")) continue;
    out.push({
      prefix: aliasGlob.slice(0, -1), // "~/*" -> "~/"
      target: resolve(baseAbs, targetGlob.slice(0, -1)),
    });
  }
  return out;
}

function expandAlias(spec: string, aliases: AliasMapping[]): string | null {
  for (const alias of aliases) {
    if (spec.startsWith(alias.prefix)) {
      return resolve(alias.target, spec.slice(alias.prefix.length));
    }
  }
  return null;
}

function collectSymbols(file: SourceFile): { exports: string[]; symbols: string[]; defaultExport: string | null } {
  const exports = new Set<string>();
  const symbols = new Set<string>();
  let defaultExport: string | null = null;

  for (const decl of file.getFunctions()) {
    const name = decl.getName();
    if (name) {
      symbols.add(name);
      if (decl.isExported()) exports.add(name);
      if (decl.isDefaultExport()) defaultExport = name;
    }
  }
  for (const cls of file.getClasses()) {
    const name = cls.getName();
    if (name) {
      symbols.add(name);
      if (cls.isExported()) exports.add(name);
      if (cls.isDefaultExport()) defaultExport = name;
    }
  }
  for (const iface of file.getInterfaces()) {
    const name = iface.getName();
    symbols.add(name);
    if (iface.isExported()) exports.add(name);
  }
  for (const typeAlias of file.getTypeAliases()) {
    const name = typeAlias.getName();
    symbols.add(name);
    if (typeAlias.isExported()) exports.add(name);
  }
  for (const enumDecl of file.getEnums()) {
    const name = enumDecl.getName();
    symbols.add(name);
    if (enumDecl.isExported()) exports.add(name);
  }
  for (const variable of file.getVariableStatements()) {
    for (const decl of variable.getDeclarations()) {
      const name = decl.getName();
      symbols.add(name);
      if (variable.isExported()) exports.add(name);
    }
  }

  // Capture re-exports such as ``export { foo } from "./other"``.
  for (const exportDecl of file.getExportDeclarations()) {
    for (const named of exportDecl.getNamedExports()) {
      const name = named.getAliasNode()?.getText() ?? named.getName();
      exports.add(name);
    }
  }

  // Capture ``export default <expression>`` patterns.
  const defaultAssignments = file.getExportAssignments();
  for (const assign of defaultAssignments) {
    if (!assign.isExportEquals()) {
      const expr = assign.getExpression();
      if (expr.getKind() === SyntaxKind.Identifier) {
        defaultExport = defaultExport ?? expr.getText();
      }
    }
  }

  return { exports: [...exports].sort(), symbols: [...symbols].sort(), defaultExport };
}

function collectImports(file: SourceFile): string[] {
  const out = new Set<string>();
  for (const importDecl of file.getImportDeclarations()) {
    out.add(importDecl.getModuleSpecifierValue());
  }
  // Pick up dynamic ``import()`` calls and ``require`` calls so the graph is
  // not blind to runtime-resolved deps.
  file.forEachDescendant((node: Node) => {
    if (node.getKind() === SyntaxKind.CallExpression) {
      const text = node.getText();
      const match = text.match(/^(?:require|import)\(['"]([^'"]+)['"]\)$/);
      if (match) {
        out.add(match[1]);
      }
    }
  });
  return [...out].sort();
}

function buildGraph(repoAbs: string): {
  files: FileNode[];
  symbolsIndex: Record<string, string>;
  hotspots: string[];
  language: string;
} {
  const project = new Project({ skipAddingFilesFromTsConfig: true });
  project.addSourceFilesAtPaths(SOURCE_GLOBS.map((g) => resolve(repoAbs, g)));
  const aliases = loadAliases(repoAbs);

  const sources = project.getSourceFiles().filter((sf: SourceFile) => {
    const fp = sf.getFilePath();
    return !IGNORE_GLOBS.some((glob) => {
      // Crude path-segment match: ``**/foo/**`` excludes any file path that
      // contains ``/foo/`` as a segment. Good enough given the IGNORE_GLOBS
      // list is short and well-known.
      const segs = glob.split("/").filter((s) => s && s !== "**");
      return segs.every((seg: string) => fp.includes(`/${seg}/`));
    });
  });

  const importedByCount = new Map<string, number>();
  const filesByRel = new Map<string, SourceFile>();
  for (const sf of sources) {
    filesByRel.set(relativePath(repoAbs, sf.getFilePath()), sf);
  }

  // Count imported_by_count for resolvable imports — relative paths plus any
  // path-alias forms declared in ``tsconfig.json`` (e.g. T3's ``~/`` prefix).
  for (const sf of sources) {
    const sourceDir = dirname(sf.getFilePath());
    for (const imp of sf.getImportDeclarations()) {
      const spec = imp.getModuleSpecifierValue();
      let baseAbs: string | null = null;
      if (spec.startsWith(".")) {
        baseAbs = resolve(sourceDir, spec);
      } else {
        baseAbs = expandAlias(spec, aliases);
      }
      if (!baseAbs) continue;
      const candidates = [
        baseAbs + ".ts",
        baseAbs + ".tsx",
        baseAbs + ".js",
        baseAbs + ".jsx",
        resolve(baseAbs, "index.ts"),
        resolve(baseAbs, "index.tsx"),
        resolve(baseAbs, "index.js"),
        resolve(baseAbs, "index.jsx"),
      ];
      for (const candidate of candidates) {
        const rel = relativePath(repoAbs, candidate);
        if (filesByRel.has(rel)) {
          importedByCount.set(rel, (importedByCount.get(rel) ?? 0) + 1);
          break;
        }
      }
    }
  }

  const files: FileNode[] = [];
  const symbolsIndex: Record<string, string> = {};
  for (const [rel, sf] of [...filesByRel.entries()].sort()) {
    const { exports, symbols, defaultExport } = collectSymbols(sf);
    const imports = collectImports(sf);
    const importedBy = importedByCount.get(rel) ?? 0;
    const isHotspot = importedBy >= HOTSPOT_THRESHOLD;
    files.push({
      path: rel,
      imports,
      exports,
      symbols,
      default_export: defaultExport,
      is_hotspot: isHotspot,
      imported_by_count: importedBy,
    });
    for (const sym of exports) {
      if (!(sym in symbolsIndex)) symbolsIndex[sym] = rel;
    }
    for (const sym of symbols) {
      if (!(sym in symbolsIndex)) symbolsIndex[sym] = rel;
    }
  }

  const hotspots = files.filter((f) => f.is_hotspot).map((f) => f.path).sort();

  // Heuristic: predominantly .ts/.tsx → typescript; otherwise javascript.
  const tsCount = files.filter((f) => /\.tsx?$/.test(f.path)).length;
  const language = tsCount >= files.length / 2 ? "typescript" : "javascript";

  return { files, symbolsIndex, hotspots, language };
}

function main(): void {
  const { repo, out } = parseArgs(process.argv.slice(2));
  const repoAbs = resolve(repo);
  const { files, symbolsIndex, hotspots, language } = buildGraph(repoAbs);

  const payload = {
    version: "1.0",
    scanned_at: new Date().toISOString(),
    root: repoAbs,
    language,
    files,
    symbols_index: symbolsIndex,
    hotspots,
  };

  const outAbs = resolve(out);
  mkdirSync(dirname(outAbs), { recursive: true });
  writeFileSync(outAbs, JSON.stringify(payload, null, 2) + "\n");
  process.stdout.write(
    `wrote ${outAbs} (${files.length} files, ${hotspots.length} hotspots, language=${language})\n`,
  );
}

main();
