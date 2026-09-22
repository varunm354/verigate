// Candidate signal matrix: twelve horizontal tracks on one shared
// 0-100 confidence scale.
//
// Encodings, deliberately doubled so none of them is colour-alone:
//   task         hue (matching the research figures) + marker outline
//   condition    marker shape — A hollow circle, B filled square,
//                C hollow diamond
//   B-A / C-B    segment length between markers + a signed numeral
//   eligibility  hatched gutter + a valid-green rule, plus the word
//                "eligible" in the accessible name and the data table
//   adjudication a monospace label, intentionally secondary
//
// Filtering animates the tracks (one of the three animated systems on
// this page) but never gates content: the data table below carries the
// same rows, and reduced motion removes the transition entirely.
"use client";

import * as React from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { RotateCcw } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatSigned } from "@/lib/format";
import {
  ADJUDICATION_LABELS,
  candidateLabel,
  researchSnapshot,
  type AdjudicationCategory,
  type Candidate,
  type TaskId,
} from "@/lib/research-data";
import { cn } from "@/lib/utils";

type TaskFilter = "all" | TaskId;
type AdjudicationFilter = "all" | AdjudicationCategory;
type EligibilityFilter = "all" | "eligible" | "not-eligible";

const TASK_COLOR: Record<TaskId, string> = {
  json_parser: "var(--task-json-lift)",
  package_resolver: "var(--task-pkg-lift)",
};

const TICKS = [0, 25, 50, 75, 100];

function Marker({
  condition,
  value,
  color,
}: {
  condition: "A" | "B" | "C";
  value: number;
  color: string;
}) {
  const shared = "absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2";
  return (
    <span
      aria-hidden="true"
      className={cn(
        shared,
        condition === "A" && "rounded-full border-2 bg-page",
        condition === "B" && "border-2",
        condition === "C" && "rotate-45 border-2 bg-page",
      )}
      style={{
        left: `${value}%`,
        borderColor: color,
        backgroundColor: condition === "B" ? color : undefined,
      }}
    />
  );
}

function Segment({
  from,
  to,
  color,
  dotted,
}: {
  from: number;
  to: number;
  color: string;
  dotted?: boolean;
}) {
  const left = Math.min(from, to);
  const width = Math.abs(to - from);
  return (
    <span
      aria-hidden="true"
      className="absolute top-1/2 -translate-y-1/2"
      style={{
        left: `${left}%`,
        width: `${width}%`,
        height: dotted ? 0 : 2,
        borderTop: dotted ? `2px dotted ${color}` : undefined,
        backgroundColor: dotted ? undefined : color,
        opacity: 0.65,
      }}
    />
  );
}

function Track({ candidate, compact = false }: { candidate: Candidate; compact?: boolean }) {
  const color = TASK_COLOR[candidate.task];
  return (
    <span className={cn("relative block", compact ? "h-7" : "h-9")}>
      <span aria-hidden="true" className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-rule" />
      {TICKS.map((t) => (
        <span
          key={t}
          aria-hidden="true"
          className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-rule"
          style={{ left: `${t}%` }}
        />
      ))}
      <Segment from={candidate.meanConfidenceA} to={candidate.meanConfidenceB} color={color} />
      <Segment from={candidate.meanConfidenceB} to={candidate.meanConfidenceC} color={color} dotted />
      <Marker condition="A" value={candidate.meanConfidenceA} color={color} />
      <Marker condition="B" value={candidate.meanConfidenceB} color={color} />
      <Marker condition="C" value={candidate.meanConfidenceC} color={color} />
    </span>
  );
}

function describe(candidate: Candidate) {
  return [
    candidateLabel(candidate),
    candidate.task,
    `A ${candidate.meanConfidenceA.toFixed(1)}`,
    `B ${candidate.meanConfidenceB.toFixed(1)}`,
    `C ${candidate.meanConfidenceC.toFixed(1)}`,
    `B minus A ${formatSigned(candidate.bMinusA, 1)}`,
    `C minus B ${formatSigned(candidate.cMinusB, 1)}`,
    ADJUDICATION_LABELS[candidate.adjudication],
    candidate.eligibleForPrimaryAnalysis ? "eligible for the primary analysis" : "not eligible",
  ].join(", ");
}

export function CandidateMatrix() {
  const [taskFilter, setTaskFilter] = React.useState<TaskFilter>("all");
  const [adjudicationFilter, setAdjudicationFilter] = React.useState<AdjudicationFilter>("all");
  const [eligibilityFilter, setEligibilityFilter] = React.useState<EligibilityFilter>("all");
  const [selected, setSelected] = React.useState<Candidate | null>(null);
  const shouldReduceMotion = useReducedMotion();
  // The dialog is state-controlled rather than wrapped around a
  // DialogTrigger, so return focus to the track that opened it.
  const lastTrigger = React.useRef<HTMLButtonElement | null>(null);

  const candidates = researchSnapshot.candidates;

  const visible = React.useMemo(
    () =>
      candidates.filter((c) => {
        if (taskFilter !== "all" && c.task !== taskFilter) return false;
        if (adjudicationFilter !== "all" && c.adjudication !== adjudicationFilter) return false;
        if (eligibilityFilter === "eligible" && !c.eligibleForPrimaryAnalysis) return false;
        if (eligibilityFilter === "not-eligible" && c.eligibleForPrimaryAnalysis) return false;
        return true;
      }),
    [candidates, taskFilter, adjudicationFilter, eligibilityFilter],
  );

  const hasFilters =
    taskFilter !== "all" || adjudicationFilter !== "all" || eligibilityFilter !== "all";

  function resetFilters() {
    setTaskFilter("all");
    setAdjudicationFilter("all");
    setEligibilityFilter("all");
  }

  const transition = shouldReduceMotion
    ? { duration: 0 }
    : { duration: 0.28, ease: [0.22, 1, 0.36, 1] as const };

  return (
    <div>
      {/* Filters ---------------------------------------------------- */}
      <form
        aria-label="Filter candidates"
        onSubmit={(e) => e.preventDefault()}
        className="flex flex-wrap items-end gap-x-6 gap-y-4 border-y border-rule py-4"
      >
        <Select
          id="filter-task"
          label="Task"
          value={taskFilter}
          onChange={(v) => setTaskFilter(v as TaskFilter)}
          options={[
            ["all", "All tasks"],
            ["json_parser", "json_parser"],
            ["package_resolver", "package_resolver"],
          ]}
        />
        <Select
          id="filter-adjudication"
          label="Adjudication"
          value={adjudicationFilter}
          onChange={(v) => setAdjudicationFilter(v as AdjudicationFilter)}
          options={[
            ["all", "All categories"],
            ["valid_pass", "Valid pass"],
            ["valid_failure", "Valid failure"],
            ["benchmark_mismatch", "Benchmark mismatch"],
            ["ambiguous", "Ambiguous"],
          ]}
        />
        <Select
          id="filter-eligibility"
          label="Eligibility"
          value={eligibilityFilter}
          onChange={(v) => setEligibilityFilter(v as EligibilityFilter)}
          options={[
            ["all", "All candidates"],
            ["eligible", "Eligible only"],
            ["not-eligible", "Not eligible"],
          ]}
        />
        <button
          type="button"
          onClick={resetFilters}
          disabled={!hasFilters}
          className="t-meta ml-auto inline-flex items-center gap-2 border border-rule px-3 py-2 text-mute transition-colors hover:border-rule-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Reset
        </button>
      </form>

      {/* Legend ----------------------------------------------------- */}
      <div className="mt-4 flex flex-wrap items-center gap-x-7 gap-y-2">
        <LegendItem>
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rounded-full border-2"
            style={{ borderColor: "var(--mute)" }}
          />
          A · no stated result
        </LegendItem>
        <LegendItem>
          <span aria-hidden="true" className="h-2.5 w-2.5" style={{ backgroundColor: "var(--mute)" }} />
          B · visible pass stated
        </LegendItem>
        <LegendItem>
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rotate-45 border-2"
            style={{ borderColor: "var(--mute)" }}
          />
          C · adversarial
        </LegendItem>
        <LegendItem>
          <span aria-hidden="true" className="h-2.5 w-2.5" style={{ backgroundColor: "var(--task-json-lift)" }} />
          json_parser
        </LegendItem>
        <LegendItem>
          <span aria-hidden="true" className="h-2.5 w-2.5" style={{ backgroundColor: "var(--task-pkg-lift)" }} />
          package_resolver
        </LegendItem>
        <LegendItem>
          <span aria-hidden="true" className="hatch-neutral h-3.5 w-2.5 border-l-2 border-valid" />
          primary-analysis eligible
        </LegendItem>
      </div>

      <p className="t-annot mt-6 text-faint" role="status">
        Showing {visible.length} of {candidates.length} candidates.
      </p>

      {/* Axis ruler. The compact form keeps a scale reference on narrow
          screens, where the full three-column header does not fit. */}
      <div className="mono mt-4 flex justify-between text-[0.6rem] uppercase tracking-wider text-faint md:hidden">
        <span>0</span>
        <span>Mean reviewer confidence</span>
        <span>100</span>
      </div>

      <div className="mono mt-4 hidden text-[0.6rem] uppercase tracking-wider text-faint md:grid md:grid-cols-[4rem_minmax(0,1fr)_10.5rem] md:items-end md:gap-x-4">
        <span>Candidate</span>
        <div>
          <p>Mean reviewer confidence</p>
          <div className="mt-1 flex justify-between">
            {TICKS.map((t) => (
              <span key={t}>{t}</span>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-3 text-right">
          <span>B − A</span>
          <span>C − B</span>
        </div>
      </div>

      {/* Tracks ------------------------------------------------------ */}
      {visible.length === 0 ? (
        <div className="border-y border-rule py-14 text-center">
          <p className="text-sm text-mute">No candidates match these filters.</p>
          <button
            type="button"
            onClick={resetFilters}
            className="t-meta mt-4 border border-rule px-3 py-2 text-fg hover:border-rule-strong"
          >
            Reset filters
          </button>
        </div>
      ) : (
        <ul className="mt-2 border-t border-rule">
          <AnimatePresence initial={false}>
            {visible.map((candidate) => (
              <motion.li
                key={candidate.qualificationIndex}
                layout={!shouldReduceMotion}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={transition}
                className="border-b border-rule"
              >
                <button
                  type="button"
                  onClick={(e) => {
                    lastTrigger.current = e.currentTarget;
                    setSelected(candidate);
                  }}
                  aria-label={`${describe(candidate)}. Open details.`}
                  className={cn(
                    "group relative flex w-full flex-col gap-2 py-3 pl-4 text-left transition-colors hover:bg-surface md:grid md:grid-cols-[4rem_minmax(0,1fr)_10.5rem] md:items-center md:gap-x-4 md:gap-y-0 motion-reduce:transition-none",
                    candidate.eligibleForPrimaryAnalysis
                      ? "border-l-2 border-valid"
                      : "border-l-2 border-transparent",
                  )}
                >
                  {candidate.eligibleForPrimaryAnalysis ? (
                    <span
                      aria-hidden="true"
                      className="hatch-neutral absolute inset-y-0 left-0 w-2.5 bg-valid/10"
                    />
                  ) : null}
                  <span className="flex items-center gap-2">
                    <span className="t-num text-sm text-fg">{candidateLabel(candidate)}</span>
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 md:hidden"
                      style={{ backgroundColor: TASK_COLOR[candidate.task] }}
                    />
                    <span className="mono text-[0.65rem] text-faint md:hidden">{candidate.task}</span>
                  </span>

                  <Track candidate={candidate} />

                  <span className="grid grid-cols-2 gap-x-3 md:text-right">
                    <span className="t-num text-sm text-fg">
                      <span className="mono mr-1.5 text-[0.6rem] font-normal tracking-wider text-faint md:hidden">
                        B−A
                      </span>
                      {formatSigned(candidate.bMinusA, 1)}
                    </span>
                    <span className="t-num text-sm text-fg">
                      <span className="mono mr-1.5 text-[0.6rem] font-normal tracking-wider text-faint md:hidden">
                        C−B
                      </span>
                      {formatSigned(candidate.cMinusB, 1)}
                    </span>
                    <span className="mono col-span-2 mt-1 text-[0.65rem] text-mute">
                      {ADJUDICATION_LABELS[candidate.adjudication].toLowerCase()}
                      {candidate.eligibleForPrimaryAnalysis ? " · eligible" : ""}
                    </span>
                  </span>
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}

      {/* Equivalent semantic representation --------------------------- */}
      <details className="mt-8 border border-rule">
        <summary className="t-meta cursor-pointer px-4 py-3 text-mute hover:text-fg">
          Data table — same rows, plain values
        </summary>
        <div className="overflow-x-auto border-t border-rule">
          <table className="w-full text-sm">
            <caption className="sr-only">
              All qualifying candidates currently shown, with reviewer means, effects, visible and hidden
              test counts, adjudication category and primary-analysis eligibility.
            </caption>
            <thead className="bg-surface text-left">
              <tr className="mono text-[0.65rem] uppercase tracking-wider text-faint">
                <th scope="col" className="px-4 py-3">Candidate</th>
                <th scope="col" className="px-4 py-3">Task</th>
                <th scope="col" className="px-4 py-3">A</th>
                <th scope="col" className="px-4 py-3">B</th>
                <th scope="col" className="px-4 py-3">C</th>
                <th scope="col" className="px-4 py-3">B − A</th>
                <th scope="col" className="px-4 py-3">C − B</th>
                <th scope="col" className="px-4 py-3">Visible</th>
                <th scope="col" className="px-4 py-3">Hidden</th>
                <th scope="col" className="px-4 py-3">Adjudication</th>
                <th scope="col" className="px-4 py-3">Eligible</th>
              </tr>
            </thead>
            <tbody className="mono text-xs">
              {visible.map((c) => (
                <tr key={c.qualificationIndex} className="border-t border-rule text-mute">
                  <th scope="row" className="whitespace-nowrap px-4 py-2.5 text-left font-normal text-fg">
                    {candidateLabel(c)}
                  </th>
                  <td className="whitespace-nowrap px-4 py-2.5">{c.task}</td>
                  <td className="px-4 py-2.5 tabular-nums">{c.meanConfidenceA.toFixed(1)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{c.meanConfidenceB.toFixed(1)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{c.meanConfidenceC.toFixed(1)}</td>
                  <td className="px-4 py-2.5 tabular-nums text-fg">{formatSigned(c.bMinusA, 1)}</td>
                  <td className="px-4 py-2.5 tabular-nums text-fg">{formatSigned(c.cMinusB, 1)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5">
                    {c.visiblePassedCount}/{c.visiblePassedCount + c.visibleFailedCount}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5">
                    {c.hiddenPassedCount}/{c.hiddenPassedCount + c.hiddenFailedCount}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5">{ADJUDICATION_LABELS[c.adjudication]}</td>
                  <td className="whitespace-nowrap px-4 py-2.5">
                    {c.eligibleForPrimaryAnalysis ? "yes" : "no"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <CandidateDialog
        candidate={selected}
        onClose={() => setSelected(null)}
        onRestoreFocus={() => lastTrigger.current?.focus()}
      />
    </div>
  );
}

function LegendItem({ children }: { children: React.ReactNode }) {
  return <span className="t-annot flex items-center gap-2 text-faint">{children}</span>;
}

function Select({
  id,
  label,
  value,
  onChange,
  options,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="t-meta text-faint">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mono h-9 border border-rule bg-surface px-2.5 text-xs text-fg"
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );
}

function CandidateDialog({
  candidate,
  onClose,
  onRestoreFocus,
}: {
  candidate: Candidate | null;
  onClose: () => void;
  onRestoreFocus: () => void;
}) {
  return (
    <Dialog open={candidate !== null} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        className="max-w-2xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onRestoreFocus();
        }}
      >
        {candidate ? (
          <>
            <DialogHeader>
              <DialogTitle>
                {candidateLabel(candidate)} · {candidate.task}
              </DialogTitle>
              <DialogDescription>
                {ADJUDICATION_LABELS[candidate.adjudication]} ·{" "}
                {candidate.eligibleForPrimaryAnalysis
                  ? "eligible for the primary analysis"
                  : "not eligible for the primary analysis"}
              </DialogDescription>
            </DialogHeader>

            <div className="border-t border-rule px-6 py-6">
              <p className="t-meta text-faint">Mean reviewer confidence, 0–100</p>
              <div className="mono mt-2 flex justify-between text-[0.6rem] text-faint">
                {TICKS.map((t) => (
                  <span key={t}>{t}</span>
                ))}
              </div>
              <Track candidate={candidate} />

              <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3">
                <Field label="A · no result" value={candidate.meanConfidenceA.toFixed(3)} />
                <Field label="B · visible pass" value={candidate.meanConfidenceB.toFixed(3)} />
                <Field label="C · adversarial" value={candidate.meanConfidenceC.toFixed(3)} />
                <Field label="B − A" value={formatSigned(candidate.bMinusA)} />
                <Field label="C − B" value={formatSigned(candidate.cMinusB)} />
                <Field
                  label="Visible tests"
                  value={`${candidate.visiblePassedCount}/${candidate.visiblePassedCount + candidate.visibleFailedCount}`}
                />
                <Field
                  label="Hidden tests"
                  value={`${candidate.hiddenPassedCount}/${candidate.hiddenPassedCount + candidate.hiddenFailedCount}`}
                />
              </dl>

              <p className="t-annot mt-6 max-w-[60ch] text-faint">
                Each value is a mean of three reviewer calls under that condition. Only sanitized public
                fields are shown — no hidden-test content, artifact paths or internal identifiers.
              </p>
            </div>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="t-meta text-faint">{label}</dt>
      <dd className="t-num mt-1.5 text-base text-fg">{value}</dd>
    </div>
  );
}
