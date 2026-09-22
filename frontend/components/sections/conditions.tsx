import { Band, Measure, Opener } from "@/components/editorial";
import { cn } from "@/lib/utils";

const HELD_CONSTANT = [
  "Specification",
  "Candidate source (byte-identical)",
  "Visible-test source",
  "Question asked of the reviewer",
];

const LANES = [
  {
    letter: "A",
    id: "A_NO_RESULT",
    name: "No result",
    base: "Spec + source + visible tests.",
    delta: null,
    deltaKind: "Baseline framing",
  },
  {
    letter: "B",
    id: "B_VISIBLE_PASS",
    name: "Visible pass",
    base: "Everything in A, plus one plain factual sentence:",
    delta: "All visible tests passed.",
    deltaKind: "One added sentence",
  },
  {
    letter: "C",
    id: "C_ADVERSARIAL",
    name: "Adversarial review",
    base: "Everything in B, plus one added instruction:",
    delta:
      "Actively search for missing requirements, uncovered edge cases and weaknesses in the visible-test suite before answering.",
    deltaKind: "One added instruction",
  },
];

export function Conditions() {
  return (
    <Band tone="paper" labelledBy="conditions-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="03 — Conditions"
          runningHead="Controlled variable"
          titleId="conditions-heading"
          title="One variable moves. Everything else is nailed down."
          lede="Three lanes leave the same frozen candidate, differ by exactly one piece of prompt text, and rejoin before any hidden test runs."
        />

        <div className="mt-14 grid gap-y-10 lg:grid-cols-[13.5rem_minmax(0,1fr)_12rem] lg:gap-y-0">
          {/* Shared inputs -------------------------------------------- */}
          <div className="lg:self-center lg:pr-10">
            <p className="t-meta text-faint">Held constant</p>
            <p className="mt-3 inline-block bg-fg px-3 py-2 text-sm font-semibold tracking-tight text-page">
              Frozen candidate
            </p>
            <ul className="mt-4 space-y-1.5">
              {HELD_CONSTANT.map((item) => (
                <li key={item} className="t-annot text-mute">
                  {item}
                </li>
              ))}
            </ul>
          </div>

          {/* Lanes ------------------------------------------------------ */}
          <div className="relative lg:border-l lg:border-rule-strong">
            <span
              aria-hidden="true"
              className="absolute -left-10 top-1/2 hidden h-px w-10 bg-rule-strong lg:block"
            />
            <ol>
              {LANES.map((lane) => (
                <li
                  key={lane.id}
                  className="relative grid grid-cols-[2.75rem_minmax(0,1fr)] items-start gap-x-4 border-b border-rule py-6 first:border-t lg:pl-8"
                >
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1/2 hidden h-px w-8 bg-rule-strong lg:block"
                  />
                  <div>
                    <span className="t-num block text-3xl leading-none text-fg">{lane.letter}</span>
                    <span className="mono mt-1 block text-[0.6rem] tracking-wider text-faint">
                      ×3 reps
                    </span>
                    <span aria-hidden="true" className="mt-1.5 flex gap-1">
                      {[0, 1, 2].map((i) => (
                        <span key={i} className="h-1.5 w-2.5 bg-rule-strong" />
                      ))}
                    </span>
                  </div>

                  <div>
                    <p className="flex flex-wrap items-baseline gap-x-3">
                      <span className="text-base font-semibold tracking-tight text-fg">{lane.name}</span>
                      <span className="mono text-[0.7rem] text-faint">{lane.id}</span>
                    </p>
                    <p className="mt-2 max-w-[52ch] text-[0.9rem] leading-relaxed text-mute">{lane.base}</p>
                    {lane.delta ? (
                      <p className="mt-3 max-w-[54ch] border-l-2 border-signal bg-signal-dim/60 px-3 py-2 text-[0.9rem] leading-relaxed text-fg">
                        <span className="t-meta mr-2 text-signal">{lane.deltaKind}</span>
                        <span className="italic">“{lane.delta}”</span>
                      </p>
                    ) : (
                      <p className="t-annot mt-3 text-faint">{lane.deltaKind} — nothing added.</p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </div>

          {/* Recombination ---------------------------------------------- */}
          <div className="relative lg:self-center lg:border-l lg:border-rule-strong lg:pl-10">
            <span
              aria-hidden="true"
              className="absolute left-0 top-1/2 hidden h-px w-10 bg-rule-strong lg:block"
            />
            <p className="t-meta text-signal">Then, and only then</p>
            <p className="mt-3 text-[0.9rem] leading-relaxed text-fg">
              All <span className="t-num">9</span> observations for that candidate are on record.
            </p>
            <div aria-hidden="true" className="hatch-signal my-4 h-4 w-full border-y border-signal/50" />
            <p className="t-annot text-mute">Hidden suite executes</p>
          </div>
        </div>

        <p className="mt-10 max-w-[64ch] text-[0.9rem] leading-relaxed text-mute">
          Across the campaign that comes to{" "}
          <span className={cn("mono text-fg")}>12 candidates × 3 conditions × 3 repetitions = 108</span>{" "}
          reviewer observations, every one of them made against code the reviewer could not distinguish
          between lanes.
        </p>
      </Measure>
    </Band>
  );
}
