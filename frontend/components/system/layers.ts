// Geometry and copy for the exploded experiment system.
//
// The schematic is described twice — once assembled (a compact stack of
// plates) and once exploded (a spatial schematic) — and every rendered
// coordinate is a linear interpolation between the two, driven by a
// single 0..1 progress value. Connector paths are rebuilt from the
// interpolated node rectangles, so they stay correct at every
// intermediate state instead of being separately keyframed.
//
// Coordinate space is the SVG viewBox below; it is rendered at roughly
// 1:1 on desktop so the in-diagram type stays legible.

export const VIEWBOX = { width: 640, height: 700 };

/** Visual vocabulary. Each actor class reads differently on purpose. */
export type LayerRole =
  | "input" // material handed to the system
  | "llm" // model reasoning
  | "tool" // mechanical execution, no judgment
  | "artifact" // the frozen thing under review
  | "truth" // withheld ground truth
  | "human"; // human judgment + deterministic scripting

export const ROLE_LABEL: Record<LayerRole, string> = {
  input: "Given material",
  llm: "LLM reasoning",
  tool: "Mechanical execution",
  artifact: "Frozen artifact",
  truth: "Hidden ground truth",
  human: "Human judgment + script",
};

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Layer {
  id: string;
  /** 1-8; A/B/C share step 5. */
  step: number;
  role: LayerRole;
  /** Short label drawn inside the plate. */
  label: string;
  /** Monospace tag drawn at the plate's trailing edge when exploded. */
  tag: string;
  /** One-line annotation that fades in beside the plate when exploded. */
  note?: string;
  a: Rect;
  e: Rect;
  /** Centre the in-plate label instead of left-aligning it. */
  centered?: boolean;
  /** Override the exploded label/note baselines for unusually tall plates. */
  labelDyE?: number;
  noteDyE?: number;
}

export const LAYERS: Layer[] = [
  {
    id: "spec",
    step: 1,
    role: "input",
    label: "Spec + starter + visible tests",
    tag: "INPUT",
    note: "All the generator ever sees.",
    a: { x: 100, y: 200, w: 440, h: 24 },
    e: { x: 20, y: 20, w: 290, h: 70 },
  },
  {
    id: "agent",
    step: 2,
    role: "llm",
    label: "Hidden-blind coding agent",
    tag: "LLM",
    note: "≤ 3 attempts, hidden-blind.",
    a: { x: 108, y: 230, w: 424, h: 24 },
    e: { x: 340, y: 92, w: 282, h: 70 },
  },
  {
    id: "pytest",
    step: 3,
    role: "tool",
    label: "Visible pytest loop",
    tag: "TOOL",
    note: "Pass/fail only. No judgment.",
    a: { x: 116, y: 260, w: 408, h: 24 },
    e: { x: 30, y: 178, w: 250, h: 66 },
  },
  {
    id: "frozen",
    step: 4,
    role: "artifact",
    label: "Frozen candidate",
    tag: "FROZEN",
    note: "Byte-identical in A/B/C.",
    centered: true,
    labelDyE: 62,
    noteDyE: 84,
    a: { x: 210, y: 292, w: 220, h: 42 },
    e: { x: 226, y: 268, w: 190, h: 118 },
  },
  {
    id: "reviewerA",
    step: 5,
    role: "llm",
    label: "Reviewer A",
    tag: "A",
    note: "No stated result",
    a: { x: 120, y: 342, w: 400, h: 20 },
    e: { x: 12, y: 420, w: 186, h: 70 },
  },
  {
    id: "reviewerB",
    step: 5,
    role: "llm",
    label: "Reviewer B",
    tag: "B",
    note: "+ visible tests passed",
    a: { x: 126, y: 366, w: 388, h: 20 },
    e: { x: 227, y: 420, w: 186, h: 70 },
  },
  {
    id: "reviewerC",
    step: 5,
    role: "llm",
    label: "Reviewer C",
    tag: "C",
    note: "+ adversarial prompt",
    a: { x: 132, y: 390, w: 376, h: 20 },
    e: { x: 442, y: 420, w: 186, h: 70 },
  },
  {
    id: "truth",
    step: 7,
    role: "truth",
    label: "Hidden ground truth",
    tag: "WITHHELD",
    note: "Executes only after all 9 reviews exist.",
    a: { x: 134, y: 438, w: 372, h: 24 },
    e: { x: 176, y: 566, w: 288, h: 62 },
  },
  {
    id: "adjudication",
    step: 8,
    role: "human",
    label: "Human adjudication + deterministic analysis",
    tag: "HUMAN / SCRIPT",
    a: { x: 126, y: 468, w: 388, h: 24 },
    e: { x: 116, y: 646, w: 408, h: 52 },
  },
];

/** The hidden-test barrier is a band, not a plate. */
export const BARRIER = {
  id: "barrier",
  step: 6,
  a: { x: 134, y: 418, w: 372, h: 12 },
  e: { x: 10, y: 528, w: 620, h: 20 },
};

export const LAYER_IDS = [...LAYERS.map((l) => l.id), BARRIER.id];

export function lerp(from: number, to: number, p: number) {
  return from + (to - from) * p;
}

export function lerpRect(a: Rect, e: Rect, p: number): Rect {
  return {
    x: lerp(a.x, e.x, p),
    y: lerp(a.y, e.y, p),
    w: lerp(a.w, e.w, p),
    h: lerp(a.h, e.h, p),
  };
}

export function rectById(id: string, p: number): Rect {
  if (id === BARRIER.id) return lerpRect(BARRIER.a, BARRIER.e, p);
  const layer = LAYERS.find((l) => l.id === id);
  if (!layer) throw new Error(`unknown layer ${id}`);
  return lerpRect(layer.a, layer.e, p);
}

type Side = "top" | "bottom" | "left" | "right";

function anchor(r: Rect, side: Side, t = 0.5) {
  switch (side) {
    case "top":
      return { x: r.x + r.w * t, y: r.y };
    case "bottom":
      return { x: r.x + r.w * t, y: r.y + r.h };
    case "left":
      return { x: r.x, y: r.y + r.h * t };
    case "right":
      return { x: r.x + r.w, y: r.y + r.h * t };
  }
}

export interface ConnectorSpec {
  id: string;
  from: { id: string; side: Side; t?: number };
  /** Either another layer, or a fixed point in exploded space. */
  to: { id: string; side: Side; t?: number } | { point: [number, number] };
  /** Pull strength of the cubic control points, in viewBox units. */
  bow?: number;
  /** Force the curve to leave and arrive along one axis. */
  axis?: "v" | "h";
  /** Highlighted with these layer ids. */
  owners: string[];
  /** Drawn with the withheld-evidence treatment. */
  withheld?: boolean;
}

/** Point the three reviewer branches recombine at, before the barrier. */
const CONVERGE_A: [number, number] = [320, 412];
const CONVERGE_E: [number, number] = [320, 508];

export const CONNECTORS: ConnectorSpec[] = [
  { id: "spec-agent", from: { id: "spec", side: "right" }, to: { id: "agent", side: "top", t: 0.35 }, bow: 46, axis: "h", owners: ["spec", "agent"] },
  { id: "spec-pytest", from: { id: "spec", side: "bottom", t: 0.4 }, to: { id: "pytest", side: "top", t: 0.28 }, bow: 40, axis: "v", owners: ["spec", "pytest"] },
  { id: "agent-pytest", from: { id: "agent", side: "left", t: 0.35 }, to: { id: "pytest", side: "top", t: 0.78 }, bow: 36, axis: "h", owners: ["agent", "pytest"] },
  { id: "pytest-agent", from: { id: "pytest", side: "right", t: 0.32 }, to: { id: "agent", side: "bottom", t: 0.14 }, bow: 36, axis: "h", owners: ["agent", "pytest"] },
  { id: "agent-frozen", from: { id: "agent", side: "bottom", t: 0.5 }, to: { id: "frozen", side: "top", t: 0.75 }, bow: 54, axis: "v", owners: ["agent", "frozen"] },
  { id: "frozen-a", from: { id: "frozen", side: "bottom", t: 0.5 }, to: { id: "reviewerA", side: "top", t: 0.5 }, bow: 34, axis: "v", owners: ["frozen", "reviewerA"] },
  { id: "frozen-b", from: { id: "frozen", side: "bottom", t: 0.5 }, to: { id: "reviewerB", side: "top", t: 0.5 }, bow: 34, axis: "v", owners: ["frozen", "reviewerB"] },
  { id: "frozen-c", from: { id: "frozen", side: "bottom", t: 0.5 }, to: { id: "reviewerC", side: "top", t: 0.5 }, bow: 34, axis: "v", owners: ["frozen", "reviewerC"] },
  { id: "a-converge", from: { id: "reviewerA", side: "bottom", t: 0.5 }, to: { point: CONVERGE_E }, bow: 20, axis: "v", owners: ["reviewerA"] },
  { id: "b-converge", from: { id: "reviewerB", side: "bottom", t: 0.5 }, to: { point: CONVERGE_E }, bow: 20, axis: "v", owners: ["reviewerB"] },
  { id: "c-converge", from: { id: "reviewerC", side: "bottom", t: 0.5 }, to: { point: CONVERGE_E }, bow: 20, axis: "v", owners: ["reviewerC"] },
  { id: "converge-truth", from: { id: "reviewerB", side: "bottom", t: 0.5 }, to: { id: "truth", side: "top", t: 0.5 }, bow: 26, axis: "v", owners: ["barrier", "truth"], withheld: true },
  { id: "truth-adjudication", from: { id: "truth", side: "bottom", t: 0.5 }, to: { id: "adjudication", side: "top", t: 0.5 }, bow: 22, axis: "v", owners: ["truth", "adjudication"] },
];

export function connectorPath(spec: ConnectorSpec, p: number): string {
  const fromRect = rectById(spec.from.id, p);
  const start = anchor(fromRect, spec.from.side, spec.from.t);

  let end: { x: number; y: number };
  if ("point" in spec.to) {
    end = {
      x: lerp(CONVERGE_A[0], CONVERGE_E[0], p),
      y: lerp(CONVERGE_A[1], CONVERGE_E[1], p),
    };
  } else {
    end = anchor(rectById(spec.to.id, p), spec.to.side, spec.to.t);
  }

  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const vertical = spec.axis ? spec.axis === "v" : Math.abs(dy) >= Math.abs(dx);
  // Control points always pull along the direction of travel, so every
  // curve leaves its source and enters its target the way the flow
  // reads -- no S-bends doubling back across neighbouring plates.
  const travel = (vertical ? Math.sign(dy) : Math.sign(dx)) || 1;
  const bow = (spec.bow ?? 30) * (0.25 + 0.75 * p) * travel;

  const c1 = vertical ? { x: start.x, y: start.y + bow } : { x: start.x + bow, y: start.y };
  const c2 = vertical ? { x: end.x, y: end.y - bow } : { x: end.x - bow, y: end.y };

  const r = (n: number) => Math.round(n * 10) / 10;
  return `M ${r(start.x)} ${r(start.y)} C ${r(c1.x)} ${r(c1.y)} ${r(c2.x)} ${r(c2.y)} ${r(end.x)} ${r(end.y)}`;
}

/** Copy shown in the methodology steps and the mobile sequential view. */
export interface SystemStep {
  n: number;
  /** Layer ids this step highlights. */
  layers: string[];
  title: string;
  body: string;
  actor: string;
}

export const SYSTEM_STEPS: SystemStep[] = [
  {
    n: 1,
    layers: ["spec"],
    title: "Specification, starter, visible tests",
    actor: ROLE_LABEL.input,
    body: "A written spec, unimplemented starter code and a public test suite. This is the entire universe the generator is given.",
  },
  {
    n: 2,
    layers: ["agent"],
    title: "Hidden-blind coding agent",
    actor: ROLE_LABEL.llm,
    body: "Up to three attempts. The agent reads its own visible-test output and nothing else — the private suite is structurally unreachable from here.",
  },
  {
    n: 3,
    layers: ["pytest"],
    title: "Visible pytest feedback loop",
    actor: ROLE_LABEL.tool,
    body: "A mechanical run of the public tests. It returns pass/fail, exercises no judgment, and is the only signal closing the generation loop.",
  },
  {
    n: 4,
    layers: ["frozen"],
    title: "The candidate source is frozen",
    actor: ROLE_LABEL.artifact,
    body: "Once the visible suite passes, the source is fixed. Every downstream reviewer reads the same bytes; nothing about the code varies again.",
  },
  {
    n: 5,
    layers: ["reviewerA", "reviewerB", "reviewerC"],
    title: "Three independent reviewer branches",
    actor: ROLE_LABEL.llm,
    body: "A, B and C fan out from that one frozen candidate — nine calls each (three conditions × three repetitions), in randomized order, all recorded before any hidden test runs.",
  },
  {
    n: 6,
    layers: ["barrier"],
    title: "The hidden-test barrier",
    actor: "Structural constraint",
    body: "Nothing crosses until all nine reviewer observations for that candidate already exist. Predictions are always made before the answer is available.",
  },
  {
    n: 7,
    layers: ["truth"],
    title: "Hidden ground truth",
    actor: ROLE_LABEL.truth,
    body: "The private suite finally executes. Its result is the thing every reviewer was asked to predict, and the thing none of them could see.",
  },
  {
    n: 8,
    layers: ["adjudication"],
    title: "Human adjudication, then deterministic analysis",
    actor: ROLE_LABEL.human,
    body: "A person classifies why each hidden-suite failure happened. A seeded script — never a hand calculation — turns the sanitized artifacts into the CSVs and figures below.",
  },
];
