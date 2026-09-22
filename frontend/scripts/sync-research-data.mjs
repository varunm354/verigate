#!/usr/bin/env node
// VeriGate Milestone 13 -- deterministic frontend data/asset synchronization.
//
// Reads ONLY the already-committed, sanitized research outputs:
//   research/results/<campaign_id>/candidate_results.csv
//   research/results/<campaign_id>/condition_summary.csv
//   research/results/<campaign_id>/paired_differences.csv
//   research/figures/*.png, *.svg
//
// Writes a small, sanitized, deterministic snapshot for the static
// frontend to consume at build time (no backend, no filesystem access
// outside frontend/ at runtime):
//   frontend/data/research-snapshot.json
//   frontend/data/figures-manifest.json
//   frontend/public/figures/*.png, *.svg (byte-identical copies)
//
// Never reads the backend's gitignored raw-artifact directory, never
// calls a provider API, never writes a timestamp or a machine-specific
// path. Run with --check to verify the
// committed frontend snapshot/assets have not drifted from source,
// without writing anything.
//
// Usage:
//   node scripts/sync-research-data.mjs         # (re)generate
//   node scripts/sync-research-data.mjs --check  # verify, no writes

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { parseCsv } from "./lib/csv.mjs";

const CHECK_MODE = process.argv.includes("--check");

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = join(SCRIPT_DIR, "..");
const REPO_ROOT = join(FRONTEND_DIR, "..");

// The single canonical campaign this milestone presents. If the
// completed campaign is ever superseded, this constant (and only this
// constant) should change.
const CANONICAL_CAMPAIGN_ID = "8449581e-4097-4865-bfae-5cd8e9aef83f";

const RESULTS_DIR = join(REPO_ROOT, "research", "results", CANONICAL_CAMPAIGN_ID);
const FIGURES_SRC_DIR = join(REPO_ROOT, "research", "figures");

const DATA_OUT_DIR = join(FRONTEND_DIR, "data");
const FIGURES_OUT_DIR = join(FRONTEND_DIR, "public", "figures");

const SNAPSHOT_PATH = join(DATA_OUT_DIR, "research-snapshot.json");
const MANIFEST_PATH = join(DATA_OUT_DIR, "figures-manifest.json");

const REQUIRED_CANDIDATE_COLUMNS = [
  "qualification_index",
  "task_id",
  "visible_passed_count",
  "visible_failed_count",
  "hidden_passed_count",
  "hidden_failed_count",
  "mean_confidence_a",
  "mean_confidence_b",
  "mean_confidence_c",
  "b_minus_a",
  "c_minus_b",
  "adjudication_status",
  "eligible_for_primary_analysis",
];

const FIGURE_DEFS = [
  {
    id: "condition_confidence",
    file: "condition_confidence",
    title: "Mean A/B/C reviewer confidence: eligible set vs. complete cohort",
    alt:
      "Grouped bar chart comparing mean reviewer confidence across conditions A (no result), B (visible pass), and C (adversarial), for two populations: the n=4 primary-eligible package_resolver-only set and the complete n=12 descriptive cohort, each bar labeled with its sample size.",
    caption:
      "Mean confidence by condition for two non-interchangeable views: the n=4 eligible set (package_resolver only, post-data adjudication amendment) and the complete n=12 descriptive cohort.",
  },
  {
    id: "candidate_effects",
    file: "candidate_effects",
    title: "Per-candidate reviewer-confidence effects across all 12 qualifying candidates",
    alt:
      "Two stacked bar-chart panels sharing a Q0-Q11 candidate axis: the top panel shows each candidate's B-minus-A visible-pass effect and the bottom panel shows each candidate's C-minus-B adversarial-review effect, colored by task and marked by hatching for primary-analysis eligibility.",
    caption:
      "Per-candidate B\u2212A and C\u2212B effects for all 12 qualifying candidates (Q0\u2013Q11), colored by task; hatched bars mark the 4 candidates eligible for the primary analysis.",
  },
  {
    id: "adjudication_breakdown",
    file: "adjudication_breakdown",
    title: "Adjudication outcomes across all 12 qualifying candidates",
    alt:
      "Horizontal bar chart of adjudication category counts across all 12 qualifying candidates: valid_pass 0, valid_failure 4, benchmark_mismatch 1, ambiguous 7.",
    caption:
      "How each of the 12 candidates' hidden-test failure was adjudicated: valid_failure and valid_pass are eligible for the primary analysis; benchmark_mismatch and ambiguous are not.",
  },
];

/** @type {string[]} */
const problems = [];

function fail(message) {
  problems.push(message);
}

function readCsv(name) {
  const path = join(RESULTS_DIR, name);
  if (!existsSync(path)) {
    fail(`Missing required source file: ${relative(REPO_ROOT, path)}`);
    return [];
  }
  return parseCsv(readFileSync(path, "utf8"));
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function round(value, decimals = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function num(value) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
}

function bool(value) {
  return String(value).trim().toLowerCase() === "true";
}

// ---------------------------------------------------------------------------
// 1. Load + validate candidate_results.csv
// ---------------------------------------------------------------------------

const candidateRows = readCsv("candidate_results.csv");

if (candidateRows.length > 0) {
  const header = Object.keys(candidateRows[0]);
  for (const col of REQUIRED_CANDIDATE_COLUMNS) {
    if (!header.includes(col)) {
      fail(`candidate_results.csv is missing required column: ${col}`);
    }
  }
}

if (candidateRows.length !== 12) {
  fail(`Expected 12 qualifying candidates in candidate_results.csv, found ${candidateRows.length}`);
}

const candidates = candidateRows
  .map((row) => ({
    qualificationIndex: Number(row.qualification_index),
    task: row.task_id,
    meanConfidenceA: round(num(row.mean_confidence_a)),
    meanConfidenceB: round(num(row.mean_confidence_b)),
    meanConfidenceC: round(num(row.mean_confidence_c)),
    bMinusA: round(num(row.b_minus_a)),
    cMinusB: round(num(row.c_minus_b)),
    visiblePassedCount: num(row.visible_passed_count),
    visibleFailedCount: num(row.visible_failed_count),
    hiddenPassedCount: num(row.hidden_passed_count),
    hiddenFailedCount: num(row.hidden_failed_count),
    adjudication: row.adjudication_status,
    eligibleForPrimaryAnalysis: bool(row.eligible_for_primary_analysis),
  }))
  .sort((a, b) => a.qualificationIndex - b.qualificationIndex);

const taskCounts = candidates.reduce((acc, c) => {
  acc[c.task] = (acc[c.task] ?? 0) + 1;
  return acc;
}, /** @type {Record<string, number>} */ ({}));

const adjudicationCounts = candidates.reduce((acc, c) => {
  acc[c.adjudication] = (acc[c.adjudication] ?? 0) + 1;
  return acc;
}, /** @type {Record<string, number>} */ ({}));

for (const category of ["valid_pass", "valid_failure", "benchmark_mismatch", "ambiguous"]) {
  if (!(category in adjudicationCounts)) adjudicationCounts[category] = 0;
}

const eligibleCandidates = candidates.filter((c) => c.eligibleForPrimaryAnalysis);
const eligibleTasks = [...new Set(eligibleCandidates.map((c) => c.task))];

// ---------------------------------------------------------------------------
// 2. Load condition_summary.csv + paired_differences.csv for cohort summaries
// ---------------------------------------------------------------------------

const conditionRows = readCsv("condition_summary.csv");
const pairedRows = readCsv("paired_differences.csv");

function conditionMean(scope, taskId, condition) {
  const row = conditionRows.find(
    (r) => r.scope === scope && r.task_id === taskId && r.condition === condition,
  );
  return row ? round(num(row.mean_confidence)) : null;
}

function pairedDiff(scope, taskId) {
  const row = pairedRows.find((r) => r.scope === scope && r.task_id === taskId);
  if (!row) return { bMinusA: null, cMinusB: null, candidateCount: 0 };
  return {
    bMinusA: round(num(row.mean_b_minus_a)),
    cMinusB: round(num(row.mean_c_minus_b)),
    candidateCount: num(row.candidate_count),
  };
}

const fullCohortDiff = pairedDiff("full_cohort", "ALL");
const eligibleDiff = pairedDiff("primary_eligible", "ALL");

const cohorts = {
  fullCohort: {
    n: fullCohortDiff.candidateCount,
    taskCoverage: { json_parser: taskCounts.json_parser ?? 0, package_resolver: taskCounts.package_resolver ?? 0 },
    meanConfidenceA: conditionMean("full_cohort", "ALL", "A_NO_RESULT"),
    meanConfidenceB: conditionMean("full_cohort", "ALL", "B_VISIBLE_PASS"),
    meanConfidenceC: conditionMean("full_cohort", "ALL", "C_ADVERSARIAL"),
    bMinusA: fullCohortDiff.bMinusA,
    cMinusB: fullCohortDiff.cMinusB,
    benchmarkPassCount: 0,
    benchmarkFailCount: fullCohortDiff.candidateCount,
  },
  eligibleSet: {
    n: eligibleDiff.candidateCount,
    taskCoverage: eligibleTasks,
    meanConfidenceA: conditionMean("primary_eligible", "ALL", "A_NO_RESULT"),
    meanConfidenceB: conditionMean("primary_eligible", "ALL", "B_VISIBLE_PASS"),
    meanConfidenceC: conditionMean("primary_eligible", "ALL", "C_ADVERSARIAL"),
    bMinusA: eligibleDiff.bMinusA,
    cMinusB: eligibleDiff.cMinusB,
    benchmarkPassCount: 0,
    benchmarkFailCount: eligibleDiff.candidateCount,
  },
};

if (cohorts.fullCohort.n !== 12) fail(`Expected full-cohort n=12, computed ${cohorts.fullCohort.n}`);
if (cohorts.eligibleSet.n !== 4) fail(`Expected eligible-set n=4, computed ${cohorts.eligibleSet.n}`);
if (eligibleTasks.length !== 1 || eligibleTasks[0] !== "package_resolver") {
  fail(`Expected eligible set to be package_resolver-only, found: ${eligibleTasks.join(", ")}`);
}

// ---------------------------------------------------------------------------
// 3. Overview counts
// ---------------------------------------------------------------------------

const totalObservations = candidates.length * 9;

const overview = {
  qualifyingCandidates: candidates.length,
  totalReviewerObservations: totalObservations,
  benchmarkTasks: Object.keys(taskCounts).length,
  reviewerConditions: 3,
  repetitionsPerCondition: 3,
  fullHiddenSuitePasses: 0,
  fullHiddenSuiteTotal: candidates.length,
};

if (totalObservations !== 108) {
  fail(`Expected 108 total reviewer observations, computed ${totalObservations}`);
}

// ---------------------------------------------------------------------------
// 4. Figures: hash source, copy byte-identical into public/, write manifest
// ---------------------------------------------------------------------------

const figures = FIGURE_DEFS.map((def) => {
  const pngSrc = join(FIGURES_SRC_DIR, `${def.file}.png`);
  const svgSrc = join(FIGURES_SRC_DIR, `${def.file}.svg`);
  if (!existsSync(pngSrc)) {
    fail(`Missing source figure: ${relative(REPO_ROOT, pngSrc)}`);
    return null;
  }
  return {
    id: def.id,
    title: def.title,
    alt: def.alt,
    caption: def.caption,
    pngPath: `/figures/${def.file}.png`,
    svgPath: existsSync(svgSrc) ? `/figures/${def.file}.svg` : null,
    pngSha256: sha256File(pngSrc),
    svgSha256: existsSync(svgSrc) ? sha256File(svgSrc) : null,
  };
}).filter(Boolean);

// Snapshot metadata: what it was generated from, with repo-relative paths
// only (no absolute filesystem paths, no timestamps).
const snapshot = {
  schemaVersion: 1,
  campaignId: CANONICAL_CAMPAIGN_ID,
  sourceFiles: [
    `research/results/${CANONICAL_CAMPAIGN_ID}/candidate_results.csv`,
    `research/results/${CANONICAL_CAMPAIGN_ID}/condition_summary.csv`,
    `research/results/${CANONICAL_CAMPAIGN_ID}/paired_differences.csv`,
    "research/figures/condition_confidence.png",
    "research/figures/candidate_effects.png",
    "research/figures/adjudication_breakdown.png",
  ],
  overview,
  cohorts,
  adjudicationCounts,
  candidates,
  figures,
};

if (problems.length > 0) {
  console.error("Research data sync/check FAILED validation:\n");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// 5. Write (or, in --check mode, diff against) the snapshot + figure copies
// ---------------------------------------------------------------------------

const snapshotJson = `${JSON.stringify(snapshot, null, 2)}\n`;
const manifest = {
  schemaVersion: 1,
  campaignId: CANONICAL_CAMPAIGN_ID,
  figures: figures.map((f) => ({
    id: f.id,
    pngPath: f.pngPath,
    pngSha256: f.pngSha256,
    svgPath: f.svgPath,
    svgSha256: f.svgSha256,
  })),
};
const manifestJson = `${JSON.stringify(manifest, null, 2)}\n`;

if (CHECK_MODE) {
  const driftIssues = [];

  if (!existsSync(SNAPSHOT_PATH)) {
    driftIssues.push(`Missing committed snapshot: ${relative(FRONTEND_DIR, SNAPSHOT_PATH)} (run: npm run data:sync)`);
  } else if (readFileSync(SNAPSHOT_PATH, "utf8") !== snapshotJson) {
    driftIssues.push(`Snapshot drift: ${relative(FRONTEND_DIR, SNAPSHOT_PATH)} does not match source data (run: npm run data:sync)`);
  }

  if (!existsSync(MANIFEST_PATH)) {
    driftIssues.push(`Missing committed figures manifest: ${relative(FRONTEND_DIR, MANIFEST_PATH)} (run: npm run data:sync)`);
  } else if (readFileSync(MANIFEST_PATH, "utf8") !== manifestJson) {
    driftIssues.push(`Figures manifest drift: ${relative(FRONTEND_DIR, MANIFEST_PATH)} does not match source figures (run: npm run data:sync)`);
  }

  for (const fig of figures) {
    for (const [pathField, hashField] of [["pngPath", "pngSha256"], ["svgPath", "svgSha256"]]) {
      const publicPath = fig[pathField];
      if (!publicPath) continue;
      const diskPath = join(FRONTEND_DIR, "public", publicPath.replace(/^\//, ""));
      if (!existsSync(diskPath)) {
        driftIssues.push(`Missing copied figure asset: public${publicPath} (run: npm run data:sync)`);
        continue;
      }
      const diskHash = sha256File(diskPath);
      if (diskHash !== fig[hashField]) {
        driftIssues.push(
          `Figure byte-identity drift: public${publicPath} hash ${diskHash} != source hash ${fig[hashField]} (run: npm run data:sync)`,
        );
      }
    }
  }

  if (driftIssues.length > 0) {
    console.error("Research data DRIFT detected:\n");
    for (const issue of driftIssues) console.error(`  - ${issue}`);
    process.exit(1);
  }

  console.log("Research data check OK: frontend snapshot, manifest, and figure assets match committed source data.");
  console.log(
    `  campaign=${CANONICAL_CAMPAIGN_ID} candidates=${overview.qualifyingCandidates} observations=${overview.totalReviewerObservations} figures=${figures.length}`,
  );
  process.exit(0);
}

mkdirSync(DATA_OUT_DIR, { recursive: true });
mkdirSync(FIGURES_OUT_DIR, { recursive: true });

writeFileSync(SNAPSHOT_PATH, snapshotJson);
writeFileSync(MANIFEST_PATH, manifestJson);

for (const def of FIGURE_DEFS) {
  for (const ext of ["png", "svg"]) {
    const src = join(FIGURES_SRC_DIR, `${def.file}.${ext}`);
    if (!existsSync(src)) continue;
    const dest = join(FIGURES_OUT_DIR, `${def.file}.${ext}`);
    copyFileSync(src, dest);
    const srcHash = sha256File(src);
    const destHash = sha256File(dest);
    if (srcHash !== destHash) {
      console.error(`Byte-identity check FAILED after copy for ${def.file}.${ext}`);
      process.exit(1);
    }
  }
}

console.log("Research data sync OK:");
console.log(`  wrote ${relative(FRONTEND_DIR, SNAPSHOT_PATH)}`);
console.log(`  wrote ${relative(FRONTEND_DIR, MANIFEST_PATH)}`);
console.log(`  copied ${figures.length} figure(s) into public/figures/ (byte-identical to research/figures/)`);
console.log(
  `  campaign=${CANONICAL_CAMPAIGN_ID} candidates=${overview.qualifyingCandidates} observations=${overview.totalReviewerObservations}`,
);
