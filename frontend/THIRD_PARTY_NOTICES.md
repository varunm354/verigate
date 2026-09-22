# Third-party notices (frontend)

This file lists every third-party UI package and every third-party UI
component **source** (copied into this repository, not installed at
runtime) used by VeriGate's frontend. It exists to satisfy the
attribution/notice terms of the licenses below. VeriGate itself is
licensed under the [Apache License 2.0](../LICENSE); see [../NOTICE](../NOTICE)
for the project-level notice.

None of the packages or component sources below required any change to
VeriGate's license; all are MIT or ISC, which are compatible with
Apache-2.0 redistribution.

## Installed npm dependencies (used as runtime libraries)

| Package | Version | License | Used for |
|---|---|---|---|
| [`lucide-react`](https://github.com/lucide-icons/lucide) | ^1.47 | ISC | The small set of icons still in use: nav open/close, the figure expand affordance, the dialog close control, the candidate-filter reset, and external-link markers. |
| [`motion`](https://motion.dev) ([GitHub](https://github.com/motiondivision/motion)) | ^13 | MIT | The three animated systems described below, via `useMotionValue`, `useTransform`, `useScroll`, `useMotionValueEvent`, `animate`, and `useReducedMotion`. |
| [`@radix-ui/react-dialog`](https://github.com/radix-ui/primitives) | ^1 | MIT | Accessible focus-trapped modal primitive underlying the figure lightbox and the candidate detail dialog (`components/ui/dialog.tsx`). |
| [`clsx`](https://github.com/lukeed/clsx) | ^2 | MIT | Conditional class-name composition (`lib/utils.ts`'s `cn`). |
| [`tailwind-merge`](https://github.com/dcastil/tailwind-merge) | ^3 | MIT | Resolves conflicting Tailwind utility classes in `cn` (`lib/utils.ts`). |

All five are used as intended (imported at their public API, no
patched/vendored copies), installed via `npm install` (already reflected
in `package.json` / `package-lock.json`), and none require any runtime
network access or external API.

No charting or data-visualisation package is installed. The exploded
experiment system, the candidate signal matrix, the measurement strip,
the zero-centred effect scales, and the adjudication specimen strip are
all hand-written SVG/CSS in this repository. `airbnb/visx` was evaluated
for the candidate matrix and deliberately **not** installed: the matrix
needs one linear 0–100 scale and a fixed set of markers, which is a few
lines of arithmetic, and hand-written SVG keeps the accessible
`<table>` and the interactive tracks generated from a single data pass.

### Dependencies removed in this milestone

`class-variance-authority` and `@radix-ui/react-tooltip` were uninstalled
together with the `Button` / `Badge` / `Tooltip` components they backed;
neither has any remaining import.

## Copied / adapted component source (not installed via a CLI or registry)

| VeriGate file | Adapted from | Upstream license | Notes |
|---|---|---|---|
| `components/ui/dialog.tsx` | [shadcn/ui](https://ui.shadcn.com) `Dialog` ([source](https://github.com/shadcn-ui/ui)), wrapping `@radix-ui/react-dialog` | MIT | Structure only; restyled entirely to VeriGate's own design tokens (`app/globals.css`). Used for the figure lightbox and the candidate detail dialog. |

shadcn/ui publishes its component source under the MIT License with the
expectation that consumers copy and adapt it directly into their own
codebase (that is its distribution model — there is no npm package to
install). No upstream visual styling, images, or brand assets were
copied, and no component was copied that this project does not use.

The `Button`, `Badge`, `Card`, and `Tooltip` sources previously adapted
from shadcn/ui, and the `NumberTicker` adapted from Magic UI, were all
deleted in this milestone along with the card-grid layout they served.

## Animated systems (kept to the requested three)

1. **Exploded experiment system** (`components/system/exploded-system.tsx`)
   — a single SVG whose plates, connectors, and hidden-test barrier
   interpolate between an assembled and an exploded geometry driven by
   one `MotionValue`. Scroll progress drives it; an explicit
   `aria-pressed` "Explode system / Assemble system" toggle overrides
   scroll. Under `prefers-reduced-motion` the diagram is pinned to the
   fully exploded, fully labelled state with no transition.
2. **Methodology highlighting** (`components/sections/methodology.tsx`)
   — the sticky diagram highlights the layers owned by whichever step is
   active. Step activation comes from `IntersectionObserver` on scroll
   and from focus/click on each step button, so the same highlighting is
   reachable by keyboard alone. Mobile renders the steps sequentially
   with no sticky scroll.
3. **Candidate matrix filtering** (`components/candidate-matrix.tsx`) —
   `AnimatePresence` reorder/enter/exit on the twelve tracks as filters
   change. Disabled under `prefers-reduced-motion`; the tracks and the
   equivalent semantic `<table>` are always present in the DOM.

Every number, label, caveat, and connection is rendered server-side and
is readable with JavaScript disabled or motion disabled. No carousel,
particle background, marquee, spotlight, or cursor-following effect is
used anywhere in this frontend.

## Fonts

`Geist` and `Geist_Mono` are loaded via `next/font/google`
(part of the pre-existing frontend scaffold, unchanged by this
milestone) — not modified or newly introduced here.

## Research content (not a UI library, listed for completeness)

The candidate/task material referenced in this dashboard's copy (task
names, adjudication categories, the JSON-parser NaN/Infinity example)
originates from VeriGate's own committed research artifacts
(`research/`, `docs/`), which in turn adapt task material from
[SpecBench](https://github.com/WecoAI/SpecBench) (Weco AI, Apache-2.0);
see [`../third_party/specbench/README.md`](../third_party/specbench/README.md)
for that attribution. This frontend does not copy any SpecBench file
directly — it only displays sanitized, already-public numbers and
prose derived from VeriGate's own `research/` outputs.
