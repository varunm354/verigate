"use client";

import * as React from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_LINKS = [
  { href: "#method", label: "Method" },
  { href: "#findings", label: "Findings" },
  { href: "#candidates", label: "Candidates" },
  { href: "#limitations", label: "Limitations" },
  { href: "#reproduce", label: "Reproduce" },
];

const REPO_URL = "https://github.com/varunm354/verigate";

export function SiteNav() {
  const [open, setOpen] = React.useState(false);
  const panelId = "mobile-nav-panel";

  React.useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <header className="tone-ink sticky top-0 z-40 border-b border-rule bg-page/90 backdrop-blur-sm supports-[backdrop-filter]:bg-page/75">
      <div className="mx-auto flex max-w-[78rem] items-center justify-between gap-6 px-5 py-3 sm:px-8">
        <Link
          href="#overview"
          className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-fg"
        >
          <span
            aria-hidden="true"
            className="flex h-5 w-5 items-center justify-center border border-cobalt"
          >
            <span className="h-1.5 w-1.5 bg-cobalt" />
          </span>
          VeriGate
        </Link>

        <nav aria-label="Primary" className="hidden items-center gap-7 md:flex">
          {NAV_LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="t-meta text-faint transition-colors hover:text-fg motion-reduce:transition-none"
            >
              {link.label}
            </a>
          ))}
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="t-meta border border-rule-strong px-3 py-2 text-fg transition-colors hover:border-cobalt hover:text-cobalt motion-reduce:transition-none"
          >
            GitHub
          </a>
        </nav>

        <button
          type="button"
          className="inline-flex h-10 w-10 items-center justify-center border border-rule text-fg md:hidden"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
        </button>
      </div>

      <div
        id={panelId}
        className={cn(
          "grid overflow-hidden border-t border-rule transition-[grid-template-rows] duration-200 motion-reduce:transition-none md:hidden",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <nav aria-label="Mobile" className="min-h-0 px-5 sm:px-8">
          <ul className="flex flex-col py-2">
            {NAV_LINKS.map((link) => (
              <li key={link.href} className="border-b border-rule last:border-b-0">
                <a
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className="t-meta block py-3.5 text-mute"
                >
                  {link.label}
                </a>
              </li>
            ))}
            <li className="border-t border-rule">
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => setOpen(false)}
                className="t-meta block py-3.5 text-cobalt"
              >
                GitHub repository
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </header>
  );
}
