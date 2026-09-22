// Methodology as scrollytelling: the exploded experiment stays put on
// desktop while eight concise steps move past it, each highlighting its
// own layer. On small screens the same eight steps become a plain
// sequential rail with every explanation visible and no sticky trap.
//
// One of the three animated systems on this page. Everything it shows
// is present as text regardless of motion, JavaScript or viewport.
"use client";

import * as React from "react";
import {
  animate,
  motion,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useTransform,
} from "motion/react";

import { Band, Measure, Opener } from "@/components/editorial";
import { ExplodedSystem } from "@/components/system/exploded-system";
import { SYSTEM_STEPS, type SystemStep } from "@/components/system/layers";
import { cn } from "@/lib/utils";

const PARTIALLY_ASSEMBLED = 0.34;

const LEGEND = [
  { label: "LLM reasoning", className: "bg-[#161d31] border-cobalt" },
  { label: "Mechanical run", className: "bg-surface border-faint" },
  { label: "Frozen artifact", className: "bg-[#f2eee6] border-[#f2eee6]" },
  { label: "Hidden truth", className: "bg-[#2c1a0f] border-signal" },
  { label: "Human judgment", className: "bg-surface border-[#e9e4d9]" },
];

export function Methodology() {
  const shouldReduceMotion = useReducedMotion();
  const sectionRef = React.useRef<HTMLDivElement>(null);
  const progress = useMotionValue(PARTIALLY_ASSEMBLED);

  // `null` means nobody has pressed the toggle yet, so scroll position
  // (or reduced motion) still decides the state.
  const [manual, setManual] = React.useState<boolean | null>(null);
  const [scrollExploded, setScrollExploded] = React.useState(false);
  const [activeStep, setActiveStep] = React.useState(1);

  const exploded = manual ?? (shouldReduceMotion ? true : scrollExploded);

  const { scrollYProgress } = useScroll({
    target: sectionRef,
    offset: ["start 0.95", "start 0.12"],
  });

  useMotionValueEvent(scrollYProgress, "change", (v) => {
    if (manual !== null || shouldReduceMotion) return;
    const next = PARTIALLY_ASSEMBLED + (1 - PARTIALLY_ASSEMBLED) * Math.min(1, Math.max(0, v));
    progress.set(next);
    setScrollExploded(next > 0.72);
  });

  // Reduced motion gets the fully separated, fully labelled state
  // immediately -- that is the most legible configuration, not the
  // least.
  React.useEffect(() => {
    if (shouldReduceMotion) progress.set(1);
  }, [shouldReduceMotion, progress]);

  function toggle() {
    const next = !exploded;
    setManual(next);
    const target = next ? 1 : 0;
    if (shouldReduceMotion) {
      progress.set(target);
    } else {
      animate(progress, target, { duration: 0.7, ease: [0.22, 1, 0.36, 1] });
    }
  }

  const tilt = useTransform(progress, [0, 1], [17, 0]);

  const activeLayers = SYSTEM_STEPS.find((s) => s.n === activeStep)?.layers ?? [];

  function selectLayer(layerId: string) {
    const step = SYSTEM_STEPS.find((s) => s.layers.includes(layerId));
    if (step) setActiveStep(step.n);
  }

  return (
    <Band tone="ink" id="method" labelledBy="method-heading" className="border-b border-rule">
      <Measure className="py-16 sm:py-24">
        <Opener
          index="02 — Method"
          runningHead="Exploded experiment"
          titleId="method-heading"
          title="Take the experiment apart"
          lede="Eight layers, one frozen candidate at the centre, and a barrier the hidden tests cannot cross until every prediction is already on record."
          aside={
            <p className="t-annot text-faint lg:text-right">
              Each step names the actor responsible for it, so it is clear where judgment enters the
              pipeline and where it does not.
            </p>
          }
        />

        <div ref={sectionRef} className="mt-14 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-12">
          {/* Steps: the authoritative, keyboard-reachable representation
              of every layer. */}
          <ol className="hidden lg:block lg:pb-[12vh] lg:pt-[8vh]">
            {SYSTEM_STEPS.map((step) => (
              <Step
                key={step.n}
                step={step}
                active={activeStep === step.n}
                onActivate={() => setActiveStep(step.n)}
                onInView={() => setActiveStep(step.n)}
              />
            ))}
          </ol>

          {/* Sticky instrument, desktop only. */}
          <div className="hidden lg:block">
            <div className="sticky top-[5.5rem] flex h-[calc(100dvh-7.5rem)] flex-col gap-4 py-4">
              <div className="flex items-center justify-between gap-4">
                <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  {LEGEND.map((item) => (
                    <li key={item.label} className="flex items-center gap-1.5">
                      <span aria-hidden="true" className={cn("h-2.5 w-2.5 border", item.className)} />
                      <span className="t-annot text-faint">{item.label}</span>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  onClick={toggle}
                  aria-pressed={exploded}
                  className="t-meta shrink-0 border border-rule-strong px-3 py-2 text-fg transition-colors hover:border-cobalt hover:text-cobalt motion-reduce:transition-none"
                >
                  {exploded ? "Assemble system" : "Explode system"}
                </button>
              </div>

              <div
                className="relative min-h-0 flex-1"
                style={{ perspective: "1600px", perspectiveOrigin: "50% 45%" }}
              >
                <motion.div
                  className="absolute inset-0 flex items-center justify-center"
                  style={{ rotateX: tilt }}
                >
                  <div className="relative h-full max-w-full" style={{ aspectRatio: "640 / 700" }}>
                    <ExplodedSystem
                      progress={progress}
                      activeLayers={activeLayers}
                      onSelectLayer={selectLayer}
                      className="absolute inset-0 h-full w-full"
                    />
                  </div>
                </motion.div>
              </div>
            </div>
          </div>
        </div>

        {/* Small screens: sequential rail. No sticky scroll, no spatial
            precision required, every explanation visible by default. */}
        <ol className="mt-12 lg:hidden">
          {SYSTEM_STEPS.map((step) => (
            <li key={step.n} className="relative grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-4 pb-8">
              <div className="flex flex-col items-center">
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-1 h-3 w-3 shrink-0 border",
                    step.layers.includes("barrier")
                      ? "border-signal bg-signal/25"
                      : step.layers.includes("frozen")
                        ? "border-[#f2eee6] bg-[#f2eee6]"
                        : "border-cobalt bg-cobalt/20",
                  )}
                />
                <span aria-hidden="true" className="mt-1 w-px flex-1 bg-rule" />
              </div>
              <div>
                <p className="t-meta text-faint">
                  Step {String(step.n).padStart(2, "0")} · {step.actor}
                </p>
                <p className="t-title mt-2 text-lg text-fg">{step.title}</p>
                <p className="mt-2 text-[0.9rem] leading-relaxed text-mute">{step.body}</p>
                {step.layers.includes("barrier") ? (
                  <div aria-hidden="true" className="hatch-signal mt-4 h-4 w-full border-y border-signal/60" />
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </Measure>
    </Band>
  );
}

function Step({
  step,
  active,
  onActivate,
  onInView,
}: {
  step: SystemStep;
  active: boolean;
  onActivate: () => void;
  onInView: () => void;
}) {
  const ref = React.useRef<HTMLLIElement>(null);

  React.useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    if (!window.matchMedia("(min-width: 1024px)").matches) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) onInView();
      },
      { rootMargin: "-45% 0px -45% 0px", threshold: 0 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [onInView]);

  return (
    <li ref={ref} className="flex min-h-[34vh] flex-col justify-center">
      <button
        type="button"
        onClick={onActivate}
        onFocus={onActivate}
        aria-pressed={active}
        className="group block max-w-[42ch] text-left"
      >
        <span
          className={cn(
            "t-meta block transition-colors motion-reduce:transition-none",
            active ? "text-cobalt" : "text-faint",
          )}
        >
          Step {String(step.n).padStart(2, "0")} · {step.actor}
        </span>
        <span
          className={cn(
            "t-title mt-3 block text-[1.55rem] transition-colors motion-reduce:transition-none",
            active ? "text-fg" : "text-mute group-hover:text-fg",
          )}
        >
          {step.title}
        </span>
      </button>
      <p
        className={cn(
          "mt-3 max-w-[42ch] text-[0.95rem] leading-relaxed transition-colors motion-reduce:transition-none",
          active ? "text-mute" : "text-faint",
        )}
      >
        {step.body}
      </p>
      <span
        aria-hidden="true"
        className={cn(
          "mt-5 block h-px w-24 transition-colors motion-reduce:transition-none",
          active ? "bg-cobalt" : "bg-rule",
        )}
      />
    </li>
  );
}
