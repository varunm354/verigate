import { ArrowUpRight } from "lucide-react";

import { Band, Measure, Opener } from "@/components/editorial";
import { CAMPAIGN_ID } from "@/lib/research-data";

const REPO_BASE = "https://github.com/varunm354/verigate/blob/main";

const GUARANTEES: [string, string][] = [
  ["Artifacts", "Every result is a saved, deterministic artifact. Nothing is recomputed on page load."],
  ["Identity", "Candidate source is SHA-256 hashed, so its identity is verifiable without exposing the source."],
  ["Schedule", "A fixed, alternating generation schedule with recorded, sequential seeds."],
  ["Blindness", "Generation is hidden-blind: the generator never has access to the hidden suite."],
  ["Isolation", "Each candidate runs in an isolated workspace with a sanitized subprocess environment."],
  ["Validation", "The full campaign was validated end-to-end before any analysis ran."],
  ["Determinism", "A scripted pipeline produces the analysis and figures; unchanged inputs give byte-identical output."],
  ["Continuous integration", "GitHub Actions lints and builds both the backend and this frontend on every push."],
  ["Cost", "Reproducing the public results and figures requires no paid API calls at all."],
];

const LINKS = [
  { label: "docs/research_protocol.md", href: `${REPO_BASE}/docs/research_protocol.md` },
  { label: "docs/pilot_findings.md", href: `${REPO_BASE}/docs/pilot_findings.md` },
  { label: "research/adjudications.json", href: `${REPO_BASE}/research/adjudications.json` },
  {
    label: "Campaign result README",
    href: `${REPO_BASE}/research/results/${CAMPAIGN_ID}/README.md`,
  },
  { label: "research/figures/README.md", href: `${REPO_BASE}/research/figures/README.md` },
  { label: "LICENSE", href: `${REPO_BASE}/LICENSE` },
  { label: "NOTICE", href: `${REPO_BASE}/NOTICE` },
  { label: "GitHub repository", href: "https://github.com/varunm354/verigate" },
];

export function Reproducibility() {
  return (
    <Band tone="ink" id="reproduce" labelledBy="reproduce-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="09 — Reproduce"
          runningHead="Source of record"
          titleId="reproduce-heading"
          title="Everything above came out of these files"
          lede="This page reads a generated snapshot of committed, sanitized research outputs. You can check all of it yourself."
        />

        <div className="mt-12 grid gap-x-16 gap-y-12 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <dl className="grid gap-x-8 gap-y-0 border-t border-rule sm:grid-cols-2">
            {GUARANTEES.map(([label, body]) => (
              <div key={label} className="border-b border-rule py-4">
                <dt className="t-meta text-cobalt">{label}</dt>
                <dd className="mt-1.5 text-[0.875rem] leading-relaxed text-mute">{body}</dd>
              </div>
            ))}
          </dl>

          <div>
            <p className="t-meta text-faint">Index of source documents</p>
            <ul className="mt-4">
              {LINKS.map((link) => (
                <li key={link.href} className="border-b border-rule">
                  <a
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-baseline gap-3 py-3 text-sm text-mute transition-colors hover:text-fg motion-reduce:transition-none"
                  >
                    <span className="mono shrink-0 text-xs">{link.label}</span>
                    <span
                      aria-hidden="true"
                      className="h-px flex-1 translate-y-[-0.2em] border-b border-dotted border-rule-strong"
                    />
                    <ArrowUpRight
                      className="h-3.5 w-3.5 shrink-0 text-faint group-hover:text-cobalt"
                      aria-hidden="true"
                    />
                  </a>
                </li>
              ))}
            </ul>
            <p className="t-annot mt-5 text-faint">
              Campaign <span className="text-mute">{CAMPAIGN_ID}</span>
            </p>
          </div>
        </div>
      </Measure>
    </Band>
  );
}
