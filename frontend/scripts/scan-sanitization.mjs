#!/usr/bin/env node
// VeriGate Milestone 13 -- static sanitization scan of frontend source.
//
// Scans the frontend's own source (app/, components/, lib/, data/,
// scripts/) for accidental leakage: absolute local filesystem paths,
// API-key-shaped strings, hidden-test filenames/markers, or raw artifact
// paths that should never appear in a public dashboard. This is a
// defense-in-depth check on top of the sanitized data already enforced
// by `research/adjudications.json` / `research/results/`; it does not
// replace human review.
//
// Usage: node scripts/scan-sanitization.mjs

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = join(SCRIPT_DIR, "..");

const SCAN_DIRS = ["app", "components", "lib", "data", "scripts"].map((d) => join(FRONTEND_DIR, d));
const SCAN_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".mjs", ".json", ".css", ".md"]);
const IGNORE_DIRNAMES = new Set(["node_modules", ".next"]);
// This scanner's own source necessarily contains the literal marker
// strings it looks for (as pattern definitions); exclude it from itself.
const SELF_PATH = fileURLToPath(import.meta.url);

const PATTERNS = [
  { name: "absolute /Users/ path", pattern: /\/Users\/[A-Za-z0-9_.-]+/g },
  { name: "absolute /home/ path", pattern: /\/home\/[A-Za-z0-9_.-]+/g },
  { name: "Windows-style absolute path", pattern: /[A-Za-z]:\\\\[A-Za-z0-9_.\\\\-]+/g },
  { name: "OpenAI-shaped API key", pattern: /sk-[A-Za-z0-9]{16,}/g },
  { name: "generic API-key assignment", pattern: /(api[_-]?key)\s*[:=]\s*["'][^"'\s]{12,}["']/gi },
  { name: "hidden-test source/marker reference", pattern: /hidden_tests\/test_hidden\.py|VERIGATE_HIDDEN_TEST_SENTINEL/g },
  { name: "raw backend/data artifact path", pattern: /backend\/data\//g },
  { name: "unsupported significance claim", pattern: /\bstatistically significant\b/gi },
  { name: "unsupported causal claim", pattern: /\bproves? that\b|\bcauses?\b(?!\s+of\s+action)/gi },
];

/** @type {{file: string; issue: string; snippet: string}[]} */
const findings = [];

function walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (IGNORE_DIRNAMES.has(entry)) continue;
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      walk(full);
    } else if (full !== SELF_PATH) {
      const ext = entry.slice(entry.lastIndexOf("."));
      if (SCAN_EXTENSIONS.has(ext)) scanFile(full);
    }
  }
}

function scanFile(path) {
  const text = readFileSync(path, "utf8");
  for (const { name, pattern } of PATTERNS) {
    const matches = text.match(pattern);
    if (matches) {
      for (const m of matches) {
        findings.push({ file: relative(FRONTEND_DIR, path), issue: name, snippet: m.slice(0, 80) });
      }
    }
  }
}

for (const dir of SCAN_DIRS) walk(dir);

if (findings.length > 0) {
  console.error(`Sanitization scan found ${findings.length} potential issue(s):\n`);
  for (const f of findings) {
    console.error(`  \u2717 [${f.issue}] ${f.file}: ${JSON.stringify(f.snippet)}`);
  }
  process.exit(1);
}

console.log(`Sanitization scan OK: no forbidden patterns found across ${SCAN_DIRS.length} scanned directories.`);
process.exit(0);
