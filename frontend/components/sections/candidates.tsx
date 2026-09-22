import { Band, Measure, Opener } from "@/components/editorial";
import { CandidateMatrix } from "@/components/candidate-matrix";

export function Candidates() {
  return (
    <Band tone="ink" id="candidates" labelledBy="candidates-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="06 — Candidates"
          runningHead="Signal matrix"
          titleId="candidates-heading"
          title="Twelve candidates on one scale"
          lede="Every qualifying candidate gets a track. The three markers are its mean confidence under A, B and C; the gap between them is the effect."
          aside={
            <p className="t-annot text-faint lg:text-right">
              Task hues match the research figures. Select any track for the full record.
            </p>
          }
        />

        <div className="mt-12">
          <CandidateMatrix />
        </div>
      </Measure>
    </Band>
  );
}
