// Typed access layer over the generated research snapshot
// (`data/research-snapshot.json`). This file never reads anything
// outside `frontend/` at runtime -- the snapshot is a static JSON file
// produced ahead of time by `scripts/sync-research-data.mjs` from the
// committed, sanitized `research/results/` + `research/figures/`
// artifacts, and is bundled into the build like any other static asset.
import snapshotJson from "@/data/research-snapshot.json";

export type AdjudicationCategory = "valid_pass" | "valid_failure" | "benchmark_mismatch" | "ambiguous";
export type TaskId = "json_parser" | "package_resolver";

export interface Candidate {
  qualificationIndex: number;
  task: TaskId;
  meanConfidenceA: number;
  meanConfidenceB: number;
  meanConfidenceC: number;
  bMinusA: number;
  cMinusB: number;
  visiblePassedCount: number;
  visibleFailedCount: number;
  hiddenPassedCount: number;
  hiddenFailedCount: number;
  adjudication: AdjudicationCategory;
  eligibleForPrimaryAnalysis: boolean;
}

export interface CohortSummary {
  n: number;
  meanConfidenceA: number | null;
  meanConfidenceB: number | null;
  meanConfidenceC: number | null;
  bMinusA: number | null;
  cMinusB: number | null;
  benchmarkPassCount: number;
  benchmarkFailCount: number;
}

export interface FullCohortSummary extends CohortSummary {
  taskCoverage: { json_parser: number; package_resolver: number };
}

export interface EligibleSetSummary extends CohortSummary {
  taskCoverage: TaskId[];
}

export interface FigureMeta {
  id: "condition_confidence" | "candidate_effects" | "adjudication_breakdown";
  title: string;
  alt: string;
  caption: string;
  pngPath: string;
  svgPath: string | null;
  pngSha256: string;
  svgSha256: string | null;
}

export interface ResearchSnapshot {
  schemaVersion: number;
  campaignId: string;
  sourceFiles: string[];
  overview: {
    qualifyingCandidates: number;
    totalReviewerObservations: number;
    benchmarkTasks: number;
    reviewerConditions: number;
    repetitionsPerCondition: number;
    fullHiddenSuitePasses: number;
    fullHiddenSuiteTotal: number;
  };
  cohorts: {
    fullCohort: FullCohortSummary;
    eligibleSet: EligibleSetSummary;
  };
  adjudicationCounts: Record<AdjudicationCategory, number>;
  candidates: Candidate[];
  figures: FigureMeta[];
}

export const researchSnapshot = snapshotJson as ResearchSnapshot;

export const CAMPAIGN_ID = researchSnapshot.campaignId;

export function candidateLabel(candidate: Pick<Candidate, "qualificationIndex">) {
  return `Q${candidate.qualificationIndex}`;
}

export function taskLabel(task: TaskId) {
  return task === "json_parser" ? "json_parser" : "package_resolver";
}

export const ADJUDICATION_LABELS: Record<AdjudicationCategory, string> = {
  valid_pass: "Valid pass",
  valid_failure: "Valid failure",
  benchmark_mismatch: "Benchmark mismatch",
  ambiguous: "Ambiguous",
};
