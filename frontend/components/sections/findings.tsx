import { Band, Measure, Opener } from "@/components/editorial";
import { formatNumber, formatSigned } from "@/lib/format";
import { researchSnapshot } from "@/lib/research-data";
import { cn } from "@/lib/utils";

const EFFECT_DOMAIN = 6;

function ConfidenceRow({ letter, value }: { letter: string; value: number | null }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_3.5rem] items-center gap-3 py-2">
      <span className="mono text-xs text-faint">{letter}</span>
      <span aria-hidden="true" className="relative block h-2.5 bg-surface-2">
        <span className="absolute inset-y-0 left-0 bg-cobalt" style={{ width: `${pct}%` }} />
      </span>
      <span className="t-num text-right text-sm text-fg">{formatNumber(value)}</span>
    </div>
  );
}

function EffectScale({ label, value, caption }: { label: string; value: number | null; caption: string }) {
  const v = value ?? 0;
  const magnitude = Math.min(Math.abs(v) / EFFECT_DOMAIN, 1) * 50;

  return (
    <div>
      <p className="t-meta text-faint">{label}</p>
      <p className="t-num mt-2 text-[clamp(2.4rem,4.6vw,3.5rem)] leading-none text-fg">
        {formatSigned(value)}
      </p>
      <div aria-hidden="true" className="relative mt-4 h-8">
        <span className="absolute inset-x-0 top-1/2 h-px bg-rule" />
        <span className="absolute left-1/2 top-0 h-full w-px bg-rule-strong" />
        <span
          className="absolute top-1/2 h-1.5 -translate-y-1/2 bg-cobalt"
          style={
            v >= 0
              ? { left: "50%", width: `${magnitude}%` }
              : { right: "50%", width: `${magnitude}%` }
          }
        />
        <span
          className="absolute top-1/2 h-3.5 w-0.5 -translate-y-1/2 bg-cobalt"
          style={v >= 0 ? { left: `${50 + magnitude}%` } : { right: `${50 + magnitude}%` }}
        />
      </div>
      <div aria-hidden="true" className="mono flex justify-between pt-1 text-[0.6rem] text-faint">
        <span>−{EFFECT_DOMAIN}</span>
        <span>0</span>
        <span>+{EFFECT_DOMAIN}</span>
      </div>
      <p className="t-annot mt-2 text-faint">{caption}</p>
    </div>
  );
}

export function Findings() {
  const { fullCohort, eligibleSet } = researchSnapshot.cohorts;

  return (
    <Band tone="ink" id="findings" labelledBy="findings-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="04 — Findings"
          runningHead="Two views, never merged"
          titleId="findings-heading"
          title="Two populations. One campaign. Not interchangeable."
          lede="The complete cohort describes everything the campaign produced. The eligible set is a narrow, amended subset. Read each as what it is."
        />

        <div className="mt-14 grid gap-x-14 gap-y-16 lg:grid-cols-2">
          {/* --- Complete descriptive cohort, n = 12 ------------------- */}
          <div>
            <div className="flex items-end justify-between gap-6 border-b-2 border-fg pb-4">
              <div>
                <p className="t-meta text-faint">Complete descriptive cohort</p>
                <p className="t-title mt-2 max-w-[18ch] text-xl text-fg">
                  Every qualifying candidate, no exceptions
                </p>
              </div>
              <p className="t-num shrink-0 text-[clamp(3rem,6vw,4.5rem)] leading-[0.8] text-fg">
                {fullCohort.n}
                <span className="mono ml-1 align-super text-xs tracking-normal text-faint">n</span>
              </p>
            </div>

            <p className="t-annot mt-4 text-mute">
              Independent of the eligibility rule. json_parser {fullCohort.taskCoverage.json_parser} ·
              package_resolver {fullCohort.taskCoverage.package_resolver}
            </p>

            <div className="mt-8">
              <p className="t-meta text-faint">Mean confidence, 0–100</p>
              <div className="mt-3 divide-y divide-rule border-y border-rule">
                <ConfidenceRow letter="A" value={fullCohort.meanConfidenceA} />
                <ConfidenceRow letter="B" value={fullCohort.meanConfidenceB} />
                <ConfidenceRow letter="C" value={fullCohort.meanConfidenceC} />
              </div>
            </div>

            <div className="mt-10 grid gap-10 sm:grid-cols-2">
              <EffectScale
                label="Mean B − A"
                value={fullCohort.bMinusA}
                caption="Stated visible pass, against no stated result."
              />
              <EffectScale
                label="Mean C − B"
                value={fullCohort.cMinusB}
                caption="Adversarial instruction, against the visible-pass baseline."
              />
            </div>

            <p className="mt-8 max-w-[46ch] text-[0.95rem] leading-relaxed text-mute">
              Across all twelve, mean confidence barely moves. There is{" "}
              <span className="text-fg">no consistent visible-pass increase</span> in the full cohort.
            </p>
          </div>

          {/* --- Amended eligible set, n = 4 --------------------------- */}
          <div className="border-l-2 border-signal pl-6 sm:pl-8">
            <div className="flex items-end justify-between gap-6 border-b-2 border-signal pb-4">
              <div>
                <p className="t-meta text-signal">Amended eligible set</p>
                <p className="t-title mt-2 max-w-[18ch] text-xl text-fg">
                  package_resolver only, post-data rule
                </p>
              </div>
              <p className="t-num shrink-0 text-[clamp(3rem,6vw,4.5rem)] leading-[0.8] text-signal">
                {eligibleSet.n}
                <span className="mono ml-1 align-super text-xs tracking-normal text-faint">n</span>
              </p>
            </div>

            <p className="t-annot mt-4 text-mute">
              The primary estimand evaluated on the eligible set produced under the disclosed post-data
              adjudication amendment — not simply “the preregistered primary analysis.”
            </p>

            <div className="mt-8">
              <p className="t-meta text-faint">Mean confidence, 0–100</p>
              <div className="mt-3 divide-y divide-rule border-y border-rule">
                <ConfidenceRow letter="A" value={eligibleSet.meanConfidenceA} />
                <ConfidenceRow letter="B" value={eligibleSet.meanConfidenceB} />
                <ConfidenceRow letter="C" value={eligibleSet.meanConfidenceC} />
              </div>
            </div>

            <div className="mt-10 grid gap-10 sm:grid-cols-2">
              <EffectScale
                label="Mean B − A"
                value={eligibleSet.bMinusA}
                caption="Directional increase under the stated visible pass."
              />
              <EffectScale
                label="Mean C − B"
                value={eligibleSet.cMinusB}
                caption="Directional decrease under the adversarial instruction."
              />
            </div>

            {/* Physically attached to the n=4 view: this warning shares
                the same signal rule as the column it qualifies. */}
            <div className="mt-8 bg-signal-dim/40 px-5 py-5">
              <p className="t-meta text-signal">Post-data amendment — read with the n = 4 view</p>
              <p className="mt-3 max-w-[52ch] text-[0.9rem] leading-relaxed text-mute">
                The eligibility precedence rule producing this set was added{" "}
                <span className="text-fg">after all 12 candidates&apos; data had been collected</span>{" "}
                (Amendment 1, 2026-09-20). The A/B/C conditions and the primary/secondary estimand were
                preregistered; the rule deciding <em>which</em> candidates are eligible was not.
              </p>
              <p className="mt-3 max-w-[52ch] text-[0.9rem] leading-relaxed text-mute">
                At n = 4, drawn from a single task, this is{" "}
                <span className="text-fg">too small and too narrow for a general conclusion</span>.
              </p>
            </div>
          </div>
        </div>

        <div
          className={cn(
            "mt-14 flex flex-wrap items-baseline gap-x-6 gap-y-2 border-t border-rule pt-5",
          )}
        >
          <p className="t-meta text-signal">Do not merge these views</p>
          <p className="t-annot max-w-[72ch] text-mute">
            No claim of statistical significance, causality or universality is made anywhere in this
            report. The complete cohort does not depend on the eligibility rule; the eligible set exists
            only because of it.
          </p>
        </div>
      </Measure>
    </Band>
  );
}
