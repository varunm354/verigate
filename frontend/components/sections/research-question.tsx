import { Band, Measure } from "@/components/editorial";

const PREMISES = [
  {
    head: "Visible tests are partial evidence",
    body: "A public suite rules out the failure modes it happens to check. Nothing more.",
  },
  {
    head: "Reviewers may anchor on stated outcomes",
    body: "“Visible tests passed” is a sentence, not a proof — but it may still read as one.",
  },
  {
    head: "Ground truth arrives late, on purpose",
    body: "The private suite runs only after every prediction for that candidate is already recorded.",
  },
  {
    head: "The evaluator is part of the system",
    body: "Agent pipelines measure generated code. How well the judge is calibrated goes unmeasured.",
  },
];

export function ResearchQuestion() {
  return (
    <Band tone="paper" labelledBy="question-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <div className="grid gap-x-14 gap-y-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <div>
            <p className="t-meta text-cobalt">01 — The question</p>
            <h2
              id="question-heading"
              className="t-display mt-6 max-w-[19ch] text-[clamp(1.9rem,3.6vw,3.25rem)] text-fg"
            >
              Same code. Same question. One extra sentence.
            </h2>
            <p className="mt-8 max-w-[40ch] border-l-2 border-cobalt pl-5 text-[1.0625rem] leading-relaxed text-fg">
              When an AI reviewer is shown the same specification, the same candidate source and the same
              visible tests, does <em>being told those tests passed</em> change its stated confidence that a
              private suite will pass too — and can an adversarial instruction pull that confidence back down?
            </p>
          </div>

          <ol className="divide-y divide-rule border-t border-rule">
            {PREMISES.map((premise, i) => (
              <li key={premise.head} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-4 py-5">
                <span className="t-meta pt-1 text-faint">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <p className="text-[0.95rem] font-semibold tracking-tight text-fg">{premise.head}</p>
                  <p className="mt-1.5 text-[0.9rem] leading-relaxed text-mute">{premise.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </Measure>
    </Band>
  );
}
