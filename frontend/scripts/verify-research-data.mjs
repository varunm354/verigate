#!/usr/bin/env node
// VeriGate Milestone 13 -- focused validation of the generated frontend
// research snapshot against the canonical, publicly documented campaign
// numbers (README.md, docs/research_protocol.md,
// research/results/<campaign_id>/README.md). This is intentionally a
// plain Node assertion script rather than a test-framework dependency --
// the frontend has no other test runner, and this is the smallest thing
// that reliably catches drift or transcription errors.
//
// Usage: node scripts/verify-research-data.mjs
// Exits non-zero (and prints every failure) if any check fails.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = join(SCRIPT_DIR, "..");
const SNAPSHOT_PATH = join(FRONTEND_DIR, "data", "research-snapshot.json");

/** @type {string[]} */
const failures = [];

function check(label, condition) {
  if (!condition) failures.push(label);
}

function approx(actual, expected, epsilon = 0.01) {
  return typeof actual === "number" && Math.abs(actual - expected) <= epsilon;
}

let snapshot;
try {
  snapshot = JSON.parse(readFileSync(SNAPSHOT_PATH, "utf8"));
} catch (err) {
  console.error(`Could not read/parse ${SNAPSHOT_PATH}: ${err.message}`);
  console.error("Run `npm run data:sync` first.");
  process.exit(1);
}

// --- Canonical campaign identity -------------------------------------------

check(
  "campaignId matches canonical campaign 8449581e-4097-4865-bfae-5cd8e9aef83f",
  snapshot.campaignId === "8449581e-4097-4865-bfae-5cd8e9aef83f",
);

// --- Cohort sizes and observation counts ------------------------------------

check("overview.qualifyingCandidates === 12", snapshot.overview?.qualifyingCandidates === 12);
check("overview.totalReviewerObservations === 108", snapshot.overview?.totalReviewerObservations === 108);
check("overview.benchmarkTasks === 2", snapshot.overview?.benchmarkTasks === 2);
check("overview.reviewerConditions === 3", snapshot.overview?.reviewerConditions === 3);
check("overview.fullHiddenSuitePasses === 0", snapshot.overview?.fullHiddenSuitePasses === 0);
check("candidates array has exactly 12 entries", Array.isArray(snapshot.candidates) && snapshot.candidates.length === 12);

// --- Task counts: 6 json_parser + 6 package_resolver ------------------------

check(
  "task coverage: 6 json_parser candidates",
  snapshot.cohorts?.fullCohort?.taskCoverage?.json_parser === 6,
);
check(
  "task coverage: 6 package_resolver candidates",
  snapshot.cohorts?.fullCohort?.taskCoverage?.package_resolver === 6,
);
if (Array.isArray(snapshot.candidates)) {
  const jsonParserCount = snapshot.candidates.filter((c) => c.task === "json_parser").length;
  const packageResolverCount = snapshot.candidates.filter((c) => c.task === "package_resolver").length;
  check("candidates: exactly 6 json_parser rows", jsonParserCount === 6);
  check("candidates: exactly 6 package_resolver rows", packageResolverCount === 6);
}

// --- Adjudication category counts: 4 / 1 / 7 / 0 ----------------------------

check("adjudicationCounts.valid_failure === 4", snapshot.adjudicationCounts?.valid_failure === 4);
check("adjudicationCounts.benchmark_mismatch === 1", snapshot.adjudicationCounts?.benchmark_mismatch === 1);
check("adjudicationCounts.ambiguous === 7", snapshot.adjudicationCounts?.ambiguous === 7);
check("adjudicationCounts.valid_pass === 0", snapshot.adjudicationCounts?.valid_pass === 0);

// --- Exact n=12 full-cohort summary values ----------------------------------

const full = snapshot.cohorts?.fullCohort ?? {};
check("fullCohort.n === 12", full.n === 12);
check("fullCohort meanConfidenceA \u2248 69.528", approx(full.meanConfidenceA, 69.528));
check("fullCohort meanConfidenceB \u2248 68.639", approx(full.meanConfidenceB, 68.639));
check("fullCohort meanConfidenceC \u2248 67.583", approx(full.meanConfidenceC, 67.583));
check("fullCohort bMinusA \u2248 -0.889", approx(full.bMinusA, -0.889));
check("fullCohort cMinusB \u2248 -1.056", approx(full.cMinusB, -1.056));

// --- Exact n=4 eligible-set summary values ----------------------------------

const eligible = snapshot.cohorts?.eligibleSet ?? {};
check("eligibleSet.n === 4", eligible.n === 4);
check("eligibleSet meanConfidenceA \u2248 47.750", approx(eligible.meanConfidenceA, 47.75));
check("eligibleSet meanConfidenceB \u2248 51.917", approx(eligible.meanConfidenceB, 51.917));
check("eligibleSet meanConfidenceC \u2248 47.583", approx(eligible.meanConfidenceC, 47.583));
check("eligibleSet bMinusA \u2248 +4.167", approx(eligible.bMinusA, 4.167));
check("eligibleSet cMinusB \u2248 -4.333", approx(eligible.cMinusB, -4.333));
check(
  "eligibleSet.taskCoverage is package_resolver-only",
  Array.isArray(eligible.taskCoverage) &&
    eligible.taskCoverage.length === 1 &&
    eligible.taskCoverage[0] === "package_resolver",
);
if (Array.isArray(snapshot.candidates)) {
  const eligibleRows = snapshot.candidates.filter((c) => c.eligibleForPrimaryAnalysis);
  check("exactly 4 candidates flagged eligibleForPrimaryAnalysis", eligibleRows.length === 4);
  check(
    "every eligible candidate is package_resolver",
    eligibleRows.every((c) => c.task === "package_resolver"),
  );
  check(
    "every eligible candidate is adjudicated valid_failure",
    eligibleRows.every((c) => c.adjudication === "valid_failure"),
  );
}

// --- Figures: exactly 3, each with both hash fields present ----------------

check("figures array has exactly 3 entries", Array.isArray(snapshot.figures) && snapshot.figures.length === 3);
const expectedFigureIds = ["condition_confidence", "candidate_effects", "adjudication_breakdown"];
if (Array.isArray(snapshot.figures)) {
  for (const id of expectedFigureIds) {
    const fig = snapshot.figures.find((f) => f.id === id);
    check(`figures includes ${id}`, Boolean(fig));
    if (fig) {
      check(`${id}.pngSha256 looks like a sha256 hex digest`, /^[0-9a-f]{64}$/.test(fig.pngSha256 ?? ""));
      check(`${id}.alt has meaningful descriptive alt text`, typeof fig.alt === "string" && fig.alt.length > 40);
    }
  }
}

// --- No forbidden fields anywhere in the snapshot ---------------------------

const FORBIDDEN_KEY_PATTERNS = [
  /candidate_id/i,
  /experiment_id/i,
  /candidate_source_sha256/i,
  /generation_seed/i,
  /reviewer_seed/i,
  /traceback/i,
  /hidden_test/i,
  /api[_-]?key/i,
];

function collectKeys(value, keys = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) collectKeys(item, keys);
  } else if (value && typeof value === "object") {
    for (const [key, val] of Object.entries(value)) {
      keys.add(key);
      collectKeys(val, keys);
    }
  }
  return keys;
}

const allKeys = [...collectKeys(snapshot)];
for (const pattern of FORBIDDEN_KEY_PATTERNS) {
  const offending = allKeys.filter((k) => pattern.test(k));
  check(`no snapshot field matches forbidden pattern ${pattern}`, offending.length === 0);
}

// The public campaign id itself is a UUID and is expected/allowed (it is
// already referenced throughout README.md, docs/, and research/); strip
// its one known occurrence before checking for any *other* UUID (which
// would indicate a leaked candidate_id/experiment_id).
const knownCampaignId = typeof snapshot.campaignId === "string" ? snapshot.campaignId : "";
const snapshotTextWithoutCampaignId = JSON.stringify(snapshot).split(knownCampaignId).join("");

const FORBIDDEN_TEXT_PATTERNS = [
  { name: "absolute /Users/ path", pattern: /\/Users\/[A-Za-z0-9_-]+/ },
  { name: "absolute /home/ path", pattern: /\/home\/[A-Za-z0-9_-]+/ },
  { name: "OpenAI-shaped API key", pattern: /sk-[A-Za-z0-9]{16,}/ },
  {
    name: "UUID other than the public campaign id (would indicate a leaked candidate_id/experiment_id)",
    pattern: /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
  },
];
for (const { name, pattern } of FORBIDDEN_TEXT_PATTERNS) {
  check(`snapshot contains no ${name}`, !pattern.test(snapshotTextWithoutCampaignId));
}

// --- Report ------------------------------------------------------------------

if (failures.length > 0) {
  console.error(`Research snapshot verification FAILED (${failures.length} issue(s)):\n`);
  for (const f of failures) console.error(`  \u2717 ${f}`);
  process.exit(1);
}

console.log(`Research snapshot verification OK: all ${allKeys.size > 0 ? "checks" : "checks"} passed.`);
console.log(
  `  campaign=${snapshot.campaignId} candidates=${snapshot.overview.qualifyingCandidates} observations=${snapshot.overview.totalReviewerObservations}`,
);
process.exit(0);
