import { Band, Measure, Opener } from "@/components/editorial";

const LIMITATIONS = [
  "Only two benchmark tasks (json_parser, package_resolver) — results may not generalize to other languages, task shapes, or larger systems.",
  "Only 12 qualifying candidates across those two tasks.",
  "One generator model and one reviewer model (the same model family for both), which may introduce self-consistency effects.",
  "Three repetitions per condition per candidate — enough for a mean, not enough for inferential statistics.",
  "All 12 candidates failed at least one hidden benchmark test — there is no valid_pass candidate to compare against.",
  "The eligible set (n=4) is package_resolver-only; json_parser contributed zero eligible candidates.",
  "The eligibility precedence rule was added after all 12 candidates' data had already been collected — it was not preregistered.",
  "Ambiguity and benchmark-mismatch adjudications reduced the eligible sample from a possible 12 down to 4.",
  "No p-values or confidence intervals are computed anywhere in this study.",
  "No claim of statistical significance is made or implied.",
  "No claim of universality — this is one controlled setting, not evidence about reviewer behavior in general.",
  "Results should be treated as an exploratory pilot and methodological demonstration, not a conclusive finding.",
];

export function Limitations() {
  return (
    <Band tone="ink" id="limitations" labelledBy="limitations-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="08 — Limitations"
          runningHead="Research ledger"
          titleId="limitations-heading"
          title="Twelve constraints on every number above"
          aside={
            <p className="t-meta border-l-2 border-signal pl-3 text-signal">
              Exploratory pilot — never collapsed, never behind a toggle
            </p>
          }
        />

        <ol className="mt-12 grid gap-x-12 border-t border-rule sm:grid-cols-2">
          {LIMITATIONS.map((item, i) => (
            <li
              key={item}
              className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-4 border-b border-rule py-4"
            >
              <span className="t-meta pt-1 text-faint">{String(i + 1).padStart(2, "0")}</span>
              <p className="text-[0.9rem] leading-relaxed text-mute">{item}</p>
            </li>
          ))}
        </ol>
      </Measure>
    </Band>
  );
}
