import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Tone band. Sections alternate between the graphite instrument tone
 * and the warm paper evidence tone; the tone class re-binds the
 * semantic colour variables in `globals.css`, so children never branch
 * on which surface they are sitting on.
 */
export function Band({
  tone,
  id,
  labelledBy,
  className,
  children,
}: {
  tone: "ink" | "paper";
  id?: string;
  labelledBy?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={cn(tone === "ink" ? "tone-ink" : "tone-paper", "relative", className)}
    >
      {children}
    </section>
  );
}

/** Shared editorial measure. Wider than a card grid, narrower than full bleed. */
export function Measure({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("mx-auto w-full max-w-[78rem] px-5 sm:px-8", className)}>{children}</div>;
}

/**
 * Section opener. Deliberately asymmetric: a numbered rule and running
 * head in the margin, a display title, and an optional lede kept to a
 * narrow measure so it never becomes a full-width paragraph.
 */
export function Opener({
  index,
  runningHead,
  title,
  titleId,
  lede,
  aside,
  className,
}: {
  index: string;
  runningHead: string;
  title: React.ReactNode;
  titleId: string;
  lede?: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-x-10 gap-y-6 lg:grid-cols-[7rem_minmax(0,1fr)]", className)}>
      <div className="flex items-baseline gap-3 lg:block">
        <p className="t-meta text-cobalt">{index}</p>
        <p className="t-meta text-faint lg:mt-2">{runningHead}</p>
        <span aria-hidden="true" className="mt-3 hidden h-px w-14 bg-rule-strong lg:block" />
      </div>

      <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-[36ch]">
          <h2 id={titleId} className="t-display text-[clamp(2rem,3.4vw,3.1rem)] text-fg">
            {title}
          </h2>
          {lede ? <div className="mt-5 max-w-[46ch] text-[0.975rem] leading-relaxed text-mute">{lede}</div> : null}
        </div>
        {aside ? <div className="lg:max-w-[22rem] lg:shrink-0">{aside}</div> : null}
      </div>
    </div>
  );
}

/** Monospace label + value pair used across the instrument surfaces. */
export function Datum({
  label,
  value,
  accent,
  className,
}: {
  label: string;
  value: React.ReactNode;
  accent?: "cobalt" | "signal" | "valid";
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <span className="t-meta text-faint">{label}</span>
      <span
        className={cn(
          "t-num text-lg",
          accent === "cobalt" && "text-cobalt",
          accent === "signal" && "text-signal",
          accent === "valid" && "text-valid",
          !accent && "text-fg",
        )}
      >
        {value}
      </span>
    </div>
  );
}

/** Hairline rule with an optional monospace caption sitting on it. */
export function RuledLabel({ children, className }: { children?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-center gap-4", className)}>
      {children ? <span className="t-meta shrink-0 text-faint">{children}</span> : null}
      <span aria-hidden="true" className="h-px flex-1 bg-rule" />
    </div>
  );
}
