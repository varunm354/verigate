import { Band, Measure } from "@/components/editorial";
import { researchSnapshot } from "@/lib/research-data";

export function MeasurementStrip() {
  const { overview } = researchSnapshot;

  return (
    <Band tone="ink" id="results" labelledBy="measure-heading" className="border-b border-rule bg-surface/60">
      <Measure className="py-12 sm:py-14">
        <div className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
          <h2 id="measure-heading" className="t-meta text-cobalt">
            One completed campaign, reported whole
          </h2>
          <p className="t-annot text-faint">
            Qualifying = passed its own visible tests and entered A/B/C review. No cherry-picking.
          </p>
        </div>

        <div aria-hidden="true" className="tick-rule mt-5 h-2.5 w-full opacity-70" />

        {/* Primary readouts: oversized numerals on a shared baseline,
            separated by rules rather than boxed individually. */}
        <dl className="grid grid-cols-1 divide-y divide-rule sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          <Readout
            value={overview.qualifyingCandidates}
            label="Qualifying candidates"
            note="Each one generated, then frozen, then reviewed nine times."
          />
          <Readout
            value={overview.totalReviewerObservations}
            label="Reviewer observations"
            note="Every observation recorded before the hidden suite ran."
            className="sm:pl-8"
          />
          <Readout
            value={`${overview.fullHiddenSuitePasses}/${overview.fullHiddenSuiteTotal}`}
            label="Full hidden-suite passes"
            note="Every qualifying candidate failed at least one hidden test."
            tone="signal"
            className="sm:pl-8"
          />
        </dl>

        {/* Supporting metrics carry deliberately less weight and read as
            the arithmetic behind the numbers above. */}
        <div className="flex flex-wrap items-center gap-x-10 gap-y-4 border-t border-rule pt-5">
          <p className="t-annot text-mute">
            <span className="text-faint">tasks</span>{" "}
            <span className="text-fg">{overview.benchmarkTasks}</span>
            <span className="mx-2 text-faint">·</span>
            <span className="text-faint">conditions</span>{" "}
            <span className="text-fg">{overview.reviewerConditions}</span>
            <span className="mx-2 text-faint">·</span>
            <span className="text-faint">repetitions</span>{" "}
            <span className="text-fg">{overview.repetitionsPerCondition}</span>
          </p>
          <p className="t-annot text-faint">
            <span className="text-mute">{overview.qualifyingCandidates}</span> candidates ×{" "}
            <span className="text-mute">
              {overview.reviewerConditions} × {overview.repetitionsPerCondition}
            </span>{" "}
            calls = <span className="text-fg">{overview.totalReviewerObservations}</span> observations
          </p>
        </div>
      </Measure>
    </Band>
  );
}

function Readout({
  value,
  label,
  note,
  tone,
  className,
}: {
  value: React.ReactNode;
  label: string;
  note: string;
  tone?: "signal";
  className?: string;
}) {
  return (
    <div className={`flex flex-col py-7 ${className ?? ""}`}>
      <dd
        className={`t-num text-[clamp(3.4rem,7.5vw,5.75rem)] leading-[0.85] ${
          tone === "signal" ? "text-signal" : "text-fg"
        }`}
      >
        {value}
      </dd>
      <dt className="t-meta mt-4 text-fg">{label}</dt>
      <p className="t-annot mt-2 max-w-[34ch] text-faint">{note}</p>
    </div>
  );
}
