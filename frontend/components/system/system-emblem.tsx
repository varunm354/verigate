// Small, static, assembled rendering of the same experiment object that
// the methodology section explodes. Decorative here (the real thing,
// with its labels and explanations, lives in `#method`), so it is
// aria-hidden and carries no interaction.
"use client";

import { useMotionValue } from "motion/react";

import { ExplodedSystem } from "@/components/system/exploded-system";

export function SystemEmblem({ className }: { className?: string }) {
  const progress = useMotionValue(0.16);

  return (
    <div
      aria-hidden="true"
      className={className}
      style={{ perspective: "1100px", perspectiveOrigin: "50% 40%" }}
    >
      <div style={{ transform: "rotateX(24deg) rotateZ(-2deg)" }}>
        <ExplodedSystem progress={progress} emblem className="h-auto w-full" />
      </div>
    </div>
  );
}
