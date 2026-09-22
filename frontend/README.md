# VeriGate frontend

A static, public research dashboard for VeriGate's completed exploratory
study (campaign `8449581e-4097-4865-bfae-5cd8e9aef83f`). Built with
Next.js (App Router) + React + TypeScript + Tailwind CSS.

This app presents already-completed research; it does not run
experiments, call any LLM provider, or require a backend at runtime.
Every number and figure it displays comes from a generated snapshot
(`data/research-snapshot.json`) produced ahead of time from the
repository's committed, sanitized research outputs
(`research/results/`, `research/adjudications.json`,
`research/figures/`) — see [`../README.md`](../README.md) for the
project overview and [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)
for UI attribution.

## Data flow

```
research/results/<campaign_id>/*.csv   ┐
research/adjudications.json            ├─▶  scripts/sync-research-data.mjs  ─▶  data/research-snapshot.json
research/figures/*.png, *.svg          ┘                                    └─▶  public/figures/*.png, *.svg
```

`scripts/sync-research-data.mjs` reads only those already-committed,
sanitized files, validates the canonical campaign id / row counts /
adjudication counts, and writes a small, sorted, sanitized JSON snapshot
plus byte-identical figure copies. The Next.js app (`lib/research-data.ts`)
imports that snapshot as a static JSON module — it never reads anything
outside `frontend/` at build or run time.

```bash
npm run data:sync    # regenerate data/research-snapshot.json + public/figures/
npm run data:check   # verify the committed snapshot/assets have not drifted from source
npm run verify        # data:check + numeric/forbidden-field checks + sanitization scan
```

## Page structure

The page alternates two surfaces rather than stacking uniform cards:
`tone-ink` (graphite-black) for the instrument — hero, measurement
strip, methodology, findings, candidate matrix, limitations,
reproducibility — and `tone-paper` (warm off-white) for the evidence —
research question, conditions, figures, adjudication. Both tones bind
the same semantic tokens (`--fg`, `--rule`, `--cobalt`, `--signal`,
`--valid`), so components in `components/editorial.tsx` (`Band`,
`Measure`, `Opener`, `Datum`) render correctly on either without
branching. Tokens live in [`app/globals.css`](./app/globals.css).

`components/system/` holds the exploded experiment diagram:
`layers.ts` is the geometry and copy for the eight layers, the
hidden-test barrier, and the connectors; `exploded-system.tsx`
interpolates between the assembled and exploded geometry from a single
`MotionValue`; `system-emblem.tsx` is the static hero instance.
See [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md) for the three
animated systems and their reduced-motion behaviour.

## Getting started

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Scripts

```bash
npm run dev              # local dev server
npm run build             # production build (static; no backend required)
npm run start             # serve the production build
npm run lint               # ESLint
npm run data:sync          # regenerate the research snapshot + figure copies from ../research/
npm run data:check         # fail if the committed snapshot/assets have drifted from ../research/
npm run verify:data        # assert canonical campaign numbers + forbidden-field checks
npm run verify:sanitization # scan source for leaked paths/keys/hidden-test markers
npm run verify              # the three checks above, in sequence
```

## Deploying

This directory is deployable as a standalone Vercel project root: set
Vercel's "Root Directory" to `frontend/`. The production build consumes
only the committed `data/research-snapshot.json` and `public/figures/`
— it does not need the rest of the monorepo present at build or run
time on Vercel. Before pushing new research results, run
`npm run data:sync` from a full checkout and commit the regenerated
`data/` and `public/figures/` files.
