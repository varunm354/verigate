import { ArrowRight } from "lucide-react";

import { Band, Measure } from "@/components/editorial";
import { SystemEmblem } from "@/components/system/system-emblem";
import { CAMPAIGN_ID } from "@/lib/research-data";

const REPO_URL = "https://github.com/varunm354/verigate";

export function Hero() {
  return (
    <Band tone="ink" id="overview" labelledBy="hero-heading" className="border-b border-rule">
      <Measure className="grid gap-14 pb-16 pt-14 sm:pt-20 lg:grid-cols-[minmax(0,1fr)_23rem] lg:gap-10 lg:pb-24">
        <div className="flex flex-col">
          <p className="t-meta flex flex-wrap items-center gap-x-3 gap-y-1 border-l-2 border-signal pl-3 text-signal">
            Exploratory controlled study
            <span className="text-faint">not a significance or causal claim</span>
          </p>

          <h1
            id="hero-heading"
            className="t-display mt-8 max-w-[15ch] text-[clamp(2.6rem,7vw,5.4rem)] text-fg"
          >
            Do passing visible tests make AI reviewers overconfident?
          </h1>

          <p className="mt-8 max-w-[44ch] text-lg leading-relaxed text-mute">
            VeriGate tells an AI code reviewer that a candidate&apos;s visible tests passed, then measures
            what that sentence does to its stated confidence about a private suite it has never seen.
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-4">
            <a
              href="#findings"
              className="group inline-flex items-center gap-2 border-b-2 border-cobalt pb-1 text-sm font-medium text-fg transition-colors hover:text-cobalt"
            >
              Read the findings
              <ArrowRight
                className="h-4 w-4 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none"
                aria-hidden="true"
              />
            </a>
            <a
              href="#method"
              className="inline-flex items-center gap-2 border-b border-rule-strong pb-1 text-sm text-mute transition-colors hover:border-fg hover:text-fg"
            >
              Take the system apart
            </a>
          </div>

          <dl className="mt-auto grid grid-cols-2 gap-x-8 gap-y-5 pt-14 sm:grid-cols-4">
            <div>
              <dt className="t-meta text-faint">Campaign</dt>
              <dd className="mono mt-1.5 text-xs text-mute">{CAMPAIGN_ID.slice(0, 8)}…{CAMPAIGN_ID.slice(-7)}</dd>
            </div>
            <div>
              <dt className="t-meta text-faint">Design</dt>
              <dd className="mono mt-1.5 text-xs text-mute">within-candidate A/B/C</dd>
            </div>
            <div>
              <dt className="t-meta text-faint">Licence</dt>
              <dd className="mono mt-1.5 text-xs text-mute">Apache-2.0</dd>
            </div>
            <div>
              <dt className="t-meta text-faint">Source</dt>
              <dd className="mono mt-1.5 text-xs">
                <a
                  href={REPO_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-mute underline decoration-rule-strong underline-offset-4 hover:text-fg"
                >
                  varunm354/verigate
                </a>
              </dd>
            </div>
          </dl>
        </div>

        <div className="flex flex-col justify-center gap-4 border-rule lg:border-l lg:pl-10">
          <SystemEmblem className="mx-auto w-[min(26rem,100%)]" />
          <p className="t-annot mx-auto max-w-[30ch] text-center text-faint lg:text-left">
            The experiment, assembled. Eight layers, one frozen candidate at the centre —
            <a
              href="#method"
              className="ml-1 text-cobalt underline decoration-cobalt/40 underline-offset-4 hover:decoration-cobalt"
            >
              take it apart
            </a>
            .
          </p>
        </div>
      </Measure>
    </Band>
  );
}
