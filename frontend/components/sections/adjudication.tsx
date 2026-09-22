// Adjudication as a twelve-unit specimen strip: one unit per candidate,
// grouped and patterned by how its hidden-suite failure was classified.
// Selecting a category highlights its units and its explanation; the
// units themselves carry visible text labels, so nothing depends on the
// highlight.
"use client";

import * as React from "react";

import { Band, Measure, Opener } from "@/components/editorial";
import {
  ADJUDICATION_LABELS,
  candidateLabel,
  researchSnapshot,
  type AdjudicationCategory,
} from "@/lib/research-data";
import { cn } from "@/lib/utils";

const CATEGORY_ORDER: AdjudicationCategory[] = [
  "valid_failure",
  "benchmark_mismatch",
  "ambiguous",
  "valid_pass",
];

const CATEGORY_NOTE: Record<AdjudicationCategory, string> = {
  valid_failure:
    "A genuine specification violation. These are the only candidates the amended precedence rule lets into the primary analysis.",
  benchmark_mismatch:
    "The hidden test's own expectation contradicts the written specification — a defect in the benchmark, not in the candidate.",
  ambiguous:
    "The specification never clearly settles the failing behaviour either way, so the failure cannot be attributed with confidence.",
  valid_pass:
    "The full hidden suite passed. No candidate in this campaign reaches this category, which is why the study has no passing comparison group.",
};

const UNIT_STYLE: Record<AdjudicationCategory, string> = {
  valid_failure: "border-valid bg-valid-dim",
  benchmark_mismatch: "border-signal bg-signal-dim hatch-signal",
  ambiguous: "border-rule-strong bg-surface hatch-neutral",
  valid_pass: "border-dashed border-rule-strong bg-transparent",
};

export function Adjudication() {
  const { adjudicationCounts, candidates } = researchSnapshot;
  const [selected, setSelected] = React.useState<AdjudicationCategory | null>(null);

  const ordered = React.useMemo(
    () =>
      [...candidates].sort(
        (a, b) =>
          CATEGORY_ORDER.indexOf(a.adjudication) - CATEGORY_ORDER.indexOf(b.adjudication) ||
          a.qualificationIndex - b.qualificationIndex,
      ),
    [candidates],
  );

  return (
    <Band tone="paper" labelledBy="adjudication-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="07 — Adjudication"
          runningHead="Benchmark validity"
          titleId="adjudication-heading"
          title="All twelve failed a hidden test. Only four were actually wrong."
          lede="A hidden-suite failure is not the same thing as a defect. A human classified why each one happened, and the answer turned out to matter as much as the calibration question."
        />

        {/* Specimen strip -------------------------------------------- */}
        <ul className="mt-14 grid grid-cols-4 gap-1.5 sm:grid-cols-6 lg:grid-cols-12">
          {ordered.map((candidate) => {
            const dimmed = selected !== null && selected !== candidate.adjudication;
            const isMismatch = candidate.adjudication === "benchmark_mismatch";
            return (
              <li
                key={candidate.qualificationIndex}
                className={cn(
                  "flex h-24 flex-col justify-between border-2 p-2 transition-opacity duration-200 motion-reduce:transition-none",
                  UNIT_STYLE[candidate.adjudication],
                  dimmed && "opacity-25",
                )}
              >
                <span className="mono text-[0.6rem] leading-none text-mute">
                  {candidate.task === "json_parser" ? "json" : "pkg"}
                  {isMismatch ? " †" : ""}
                </span>
                <span>
                  <span className="t-num block text-sm text-fg">{candidateLabel(candidate)}</span>
                  <span className="sr-only">{ADJUDICATION_LABELS[candidate.adjudication]}</span>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "mt-1 block h-1",
                      candidate.adjudication === "valid_failure" && "bg-valid",
                      candidate.adjudication === "benchmark_mismatch" && "bg-signal",
                      candidate.adjudication === "ambiguous" && "bg-rule-strong",
                    )}
                  />
                </span>
              </li>
            );
          })}
        </ul>

        {/* Categories -------------------------------------------------- */}
        <div className="mt-10 grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div>
            <p className="t-meta text-faint">Select a category to isolate its specimens</p>
            <ul className="mt-3 divide-y divide-rule border-y border-rule">
              {CATEGORY_ORDER.map((category) => {
                const active = selected === category;
                const count = adjudicationCounts[category];
                return (
                  <li key={category}>
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setSelected(active ? null : category)}
                      className="group grid w-full grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-4 py-4 text-left"
                    >
                      <span
                        className={cn(
                          "t-num text-3xl leading-none",
                          category === "valid_failure" && "text-valid",
                          category === "benchmark_mismatch" && "text-signal",
                          category === "ambiguous" && "text-mute",
                          category === "valid_pass" && "text-faint",
                        )}
                      >
                        {count}
                      </span>
                      <span>
                        <span
                          className={cn(
                            "block text-[0.95rem] font-semibold tracking-tight",
                            active ? "text-fg" : "text-mute group-hover:text-fg",
                          )}
                        >
                          {ADJUDICATION_LABELS[category]}
                          <span className="mono ml-2 text-[0.65rem] font-normal text-faint">
                            {category}
                          </span>
                        </span>
                        <span
                          className={cn(
                            "mt-1.5 block max-w-[52ch] text-[0.875rem] leading-relaxed",
                            active ? "text-mute" : "text-faint",
                          )}
                        >
                          {CATEGORY_NOTE[category]}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Margin notes ------------------------------------------------ */}
          <div className="lg:pt-8">
            <div className="border-l-2 border-signal pl-5">
              <p className="t-meta text-signal">† Q0 — the benchmark was wrong</p>
              <p className="mt-2 max-w-[42ch] text-[0.9rem] leading-relaxed text-mute">
                One json_parser candidate failed hidden tests that expected{" "}
                <code className="mono bg-surface px-1 py-0.5 text-fg">NaN</code> and{" "}
                <code className="mono bg-surface px-1 py-0.5 text-fg">Infinity</code> to be accepted,
                while the written specification explicitly prohibited them. The candidate was right and
                the hidden test was wrong.
              </p>
            </div>

            <div className="mt-8 border-l-2 border-rule pl-5">
              <p className="t-meta text-faint">On the method</p>
              <p className="mt-2 max-w-[42ch] text-[0.9rem] leading-relaxed text-mute">
                Adjudication distinguished genuine failures, benchmark mismatches and ambiguous cases. It
                never altered a benchmark result to fit a preferred outcome. Benchmark validity became a
                secondary finding of this study in its own right.
              </p>
            </div>
          </div>
        </div>
      </Measure>
    </Band>
  );
}
