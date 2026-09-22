import { Band, Measure, Opener } from "@/components/editorial";
import { FigureSpread } from "@/components/figure-spread";
import { researchSnapshot, type FigureMeta } from "@/lib/research-data";

const COMMENTARY: Record<
  FigureMeta["id"],
  { reading: React.ReactNode; caveat: React.ReactNode }
> = {
  condition_confidence: {
    reading: (
      <>
        The two populations sit at different absolute levels — roughly 48–52 on the amended eligible set,
        roughly 68–70 across all twelve — and they do not move the same way between A and B.
      </>
    ),
    caveat: (
      <>
        The n = 4 bars come from package_resolver alone, under the post-data amendment. They are not a
        refinement of the n = 12 bars and cannot be substituted for them.
      </>
    ),
  },
  candidate_effects: {
    reading: (
      <>
        Individual candidates move a long way in both directions — B − A runs from about −37 to +33 — which
        is exactly why the cohort mean sits close to zero. The average hides the spread.
      </>
    ),
    caveat: (
      <>
        Hatching marks the four primary-eligible candidates. Each bar is a mean of three repetitions: enough
        for a point, not enough for an interval.
      </>
    ),
  },
  adjudication_breakdown: {
    reading: (
      <>
        Only 4 of the 12 hidden-suite failures were adjudicated as genuine candidate defects. Seven were
        ambiguous against the written specification and one was a defect in the benchmark itself.
      </>
    ),
    caveat: (
      <>
        valid_pass is empty. No candidate passed the full hidden suite, so this study contains no passing
        comparison group at all.
      </>
    ),
  },
};

export function Figures() {
  return (
    <Band tone="paper" labelledBy="figures-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="05 — Figures"
          runningHead="Deterministic charts"
          titleId="figures-heading"
          title="Three charts, generated not drawn"
          lede="Produced by a seeded script straight from the sanitized campaign CSVs. Re-running it against unchanged inputs reproduces these exact files."
        />

        <div className="mt-12">
          {researchSnapshot.figures.map((figure, i) => (
            <FigureSpread
              key={figure.id}
              figure={figure}
              index={i + 1}
              flip={i % 2 === 1}
              reading={COMMENTARY[figure.id].reading}
              caveat={COMMENTARY[figure.id].caveat}
            />
          ))}
        </div>
      </Measure>
    </Band>
  );
}
