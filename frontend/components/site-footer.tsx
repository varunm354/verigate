import { Measure } from "@/components/editorial";
import { CAMPAIGN_ID } from "@/lib/research-data";

const REPO_BASE = "https://github.com/varunm354/verigate/blob/main";

const LINKS = [
  { label: "GitHub", href: "https://github.com/varunm354/verigate" },
  { label: "Protocol", href: `${REPO_BASE}/docs/research_protocol.md` },
  { label: "Results", href: `${REPO_BASE}/research/results/${CAMPAIGN_ID}/README.md` },
  { label: "Apache-2.0 License", href: `${REPO_BASE}/LICENSE` },
  { label: "SpecBench attribution", href: `${REPO_BASE}/third_party/specbench/README.md` },
];

export function SiteFooter() {
  return (
    <footer className="tone-ink border-t border-rule py-12">
      <Measure>
        <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
          <div className="max-w-[34ch]">
            <p className="text-sm font-semibold tracking-tight text-fg">VeriGate</p>
            <p className="mt-2 text-sm leading-relaxed text-mute">
              An exploratory study of AI code-review calibration. No fabricated organization, company, lab
              or affiliation — an independent research project.
            </p>
          </div>

          <nav aria-label="Footer" className="md:min-w-[18rem]">
            <ul>
              {LINKS.map((link) => (
                <li key={link.href} className="border-b border-rule first:border-t">
                  <a
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="t-meta block py-2.5 text-faint transition-colors hover:text-fg motion-reduce:transition-none"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        <p className="t-annot mt-10 text-faint">
          campaign {CAMPAIGN_ID} · exploratory pilot · no claim of statistical significance, causality or
          universality
        </p>
      </Measure>
    </footer>
  );
}
