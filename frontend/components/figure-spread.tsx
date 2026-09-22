// One research figure, given its own editorial spread rather than a
// slot in a uniform gallery grid. The PNG bytes are served untouched
// from `public/figures/`; nothing here restyles, recolours or redraws
// the chart.
"use client";

import Image from "next/image";
import { Maximize2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { FigureMeta } from "@/lib/research-data";
import { cn } from "@/lib/utils";

const NATURAL_SIZE: Record<FigureMeta["id"], { width: number; height: number }> = {
  condition_confidence: { width: 1920, height: 1320 },
  candidate_effects: { width: 2000, height: 1360 },
  adjudication_breakdown: { width: 1920, height: 880 },
};

export interface FigureSpreadProps {
  figure: FigureMeta;
  index: number;
  reading: React.ReactNode;
  caveat: React.ReactNode;
  flip?: boolean;
}

export function FigureSpread({ figure, index, reading, caveat, flip = false }: FigureSpreadProps) {
  const size = NATURAL_SIZE[figure.id];

  return (
    <article
      className={cn(
        "grid items-start gap-x-12 gap-y-6 border-t border-rule py-12 lg:py-16",
        // The plate column stays the wide one on both sides of the spread;
        // flipping swaps the track widths, not just the visual order.
        flip
          ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.55fr)]"
          : "lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]",
      )}
    >
      <div className={cn(flip && "lg:order-2")}>
        <Dialog>
          <DialogTrigger
            asChild={false}
            className="group relative block w-full border border-rule bg-white text-left transition-colors hover:border-rule-strong focus-visible:border-rule-strong motion-reduce:transition-none"
          >
            <Image
              src={figure.pngPath}
              alt={figure.alt}
              width={size.width}
              height={size.height}
              // Served straight from `public/figures/`, never re-encoded:
              // the sha256 printed beside the figure has to keep matching
              // the committed bytes in `research/figures/`.
              unoptimized
              className="h-auto w-full"
            />
            <span className="pointer-events-none absolute bottom-3 right-3 flex items-center gap-1.5 border border-white/25 bg-black/85 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none">
              <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
              View full size
            </span>
          </DialogTrigger>
          <DialogContent className="p-0">
            <DialogHeader>
              <DialogTitle>{figure.title}</DialogTitle>
              <DialogDescription>{figure.caption}</DialogDescription>
            </DialogHeader>
            <div className="border-y border-rule bg-white p-4">
              <Image
                src={figure.pngPath}
                alt={figure.alt}
                width={size.width}
                height={size.height}
                unoptimized
                className="h-auto w-full"
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 p-5">
              <span className="mono text-xs text-faint">{figure.pngPath}</span>
              <a
                href={figure.pngPath}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-mute underline decoration-rule-strong underline-offset-4 hover:text-fg"
              >
                Open the PNG in a new tab
              </a>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className={cn("lg:pt-2", flip && "lg:order-1")}>
        <p className="t-meta text-cobalt">Figure {index}</p>
        <h3 className="t-title mt-3 max-w-[26ch] text-xl text-fg">{figure.title}</h3>
        <p className="mt-4 max-w-[42ch] text-[0.95rem] leading-relaxed text-mute">{reading}</p>

        <div className="mt-6 border-l-2 border-signal pl-4">
          <p className="t-meta text-signal">Read it with this</p>
          <p className="mt-2 max-w-[40ch] text-[0.875rem] leading-relaxed text-mute">{caveat}</p>
        </div>

        <p className="mono mt-6 text-[0.7rem] text-faint">
          {figure.pngPath.replace("/figures/", "")} · sha256 {figure.pngSha256.slice(0, 12)}…
        </p>
      </div>
    </article>
  );
}
