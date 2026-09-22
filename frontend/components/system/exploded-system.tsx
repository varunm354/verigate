// The exploded experiment system: a single SVG schematic whose every
// coordinate is interpolated between an assembled stack and a spatial
// explosion by one progress motion value (see ./layers.ts).
//
// Accessibility contract: the SVG itself is presentational. The
// authoritative, keyboard-reachable representation of all eight layers
// and their explanations is the step list rendered beside it by
// `sections/methodology.tsx` (and the sequential list on small
// screens). Clicking a plate here is a pointer shortcut to the same
// state, never the only way to reach it.
"use client";

import * as React from "react";
import { motion, useTransform, type MotionValue } from "motion/react";

import {
  BARRIER,
  CONNECTORS,
  LAYERS,
  VIEWBOX,
  connectorPath,
  lerp,
  lerpRect,
  type ConnectorSpec,
  type Layer,
  type LayerRole,
} from "@/components/system/layers";

interface RoleStyle {
  fill: string;
  stroke: string;
  text: string;
  tagText: string;
  dashed?: boolean;
}

// Fixed to the graphite instrument tone: this schematic only ever
// appears on `.tone-ink` surfaces.
const ROLE_STYLE: Record<LayerRole, RoleStyle> = {
  input: { fill: "#191b20", stroke: "#434852", text: "#edeef0", tagText: "#949aa5" },
  llm: { fill: "#161d31", stroke: "#4d7cff", text: "#dbe4ff", tagText: "#8fadff", dashed: true },
  tool: { fill: "#121317", stroke: "#7c838f", text: "#edeef0", tagText: "#949aa5" },
  artifact: { fill: "#f2eee6", stroke: "#f2eee6", text: "#15171b", tagText: "#5b5f66" },
  truth: { fill: "#2c1a0f", stroke: "#ff7a29", text: "#ffd9bf", tagText: "#ff9a58" },
  human: { fill: "#121317", stroke: "#e9e4d9", text: "#f2eee6", tagText: "#c9c2b2" },
};

const EASE_IN_RANGE = (p: number, start: number, end: number) =>
  Math.max(0, Math.min(1, (p - start) / (end - start)));

function Plate({
  layer,
  progress,
  dimmed,
  active,
  showText,
  onSelect,
}: {
  layer: Layer;
  progress: MotionValue<number>;
  dimmed: boolean;
  active: boolean;
  showText: boolean;
  onSelect?: (id: string) => void;
}) {
  const style = ROLE_STYLE[layer.role];

  const x = useTransform(progress, (p) => lerpRect(layer.a, layer.e, p).x);
  const y = useTransform(progress, (p) => lerpRect(layer.a, layer.e, p).y);
  const w = useTransform(progress, (p) => lerpRect(layer.a, layer.e, p).w);
  const h = useTransform(progress, (p) => lerpRect(layer.a, layer.e, p).h);

  const labelX = useTransform(progress, (p) => {
    const r = lerpRect(layer.a, layer.e, p);
    return layer.centered ? r.x + r.w / 2 : r.x + 14;
  });
  const labelY = useTransform(progress, (p) => {
    const r = lerpRect(layer.a, layer.e, p);
    const assembledDy = layer.a.h / 2 + 4.5;
    const explodedDy = layer.labelDyE ?? (layer.note ? 27 : layer.e.h / 2 + 5);
    return r.y + lerp(assembledDy, explodedDy, p);
  });

  const noteX = useTransform(progress, (p) => {
    const r = lerpRect(layer.a, layer.e, p);
    return layer.centered ? r.x + r.w / 2 : r.x + 14;
  });
  const noteY = useTransform(
    progress,
    (p) => lerpRect(layer.a, layer.e, p).y + (layer.noteDyE ?? 48),
  );
  const noteOpacity = useTransform(progress, (p) => EASE_IN_RANGE(p, 0.55, 0.9));

  const tagX = useTransform(progress, (p) => {
    const r = lerpRect(layer.a, layer.e, p);
    return r.x + r.w - 12;
  });
  const tagY = useTransform(progress, (p) => {
    const r = lerpRect(layer.a, layer.e, p);
    return r.y + 15;
  });
  const tagOpacity = useTransform(progress, (p) => EASE_IN_RANGE(p, 0.6, 0.95));

  // Inset dashed outline marks model reasoning; the tick column marks
  // mechanical execution. Both are secondary to the label, never the
  // only cue.
  const insetX = useTransform(x, (v) => v + 5);
  const insetY = useTransform(y, (v) => v + 5);
  const insetW = useTransform(w, (v) => Math.max(0, v - 10));
  const insetH = useTransform(h, (v) => Math.max(0, v - 10));

  const bracketX = useTransform(x, (v) => v - 7);
  const bracketY = useTransform(y, (v) => v - 7);
  const bracketW = useTransform(w, (v) => v + 14);
  const bracketH = useTransform(h, (v) => v + 14);

  return (
    <g
      className="transition-opacity duration-300 motion-reduce:transition-none"
      opacity={dimmed ? 0.45 : 1}
      onClick={onSelect ? () => onSelect(layer.id) : undefined}
      style={onSelect ? { cursor: "pointer" } : undefined}
    >
      <motion.rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={2}
        fill={style.fill}
        stroke={style.stroke}
        strokeWidth={active ? 2.5 : 1.25}
      />
      {style.dashed ? (
        <motion.rect
          x={insetX}
          y={insetY}
          width={insetW}
          height={insetH}
          rx={1}
          fill="none"
          stroke={style.stroke}
          strokeWidth={0.75}
          strokeDasharray="2 4"
          opacity={0.7}
        />
      ) : null}
      {layer.role === "tool" ? (
        <motion.rect x={x} y={y} width={4} height={h} fill="#7c838f" opacity={0.85} />
      ) : null}
      {layer.role === "human" ? (
        <motion.rect x={x} y={y} width={4} height={h} fill="#f2eee6" />
      ) : null}
      {layer.role === "truth" ? (
        <motion.rect x={x} y={y} width={4} height={h} fill="#ff7a29" />
      ) : null}

      {showText ? (
        <>
          <motion.text
            x={labelX}
            y={labelY}
            fill={style.text}
            fontSize={13.5}
            fontWeight={600}
            letterSpacing="-0.01em"
            textAnchor={layer.centered ? "middle" : "start"}
          >
            {layer.label}
          </motion.text>

          {layer.note ? (
            <motion.text
              x={noteX}
              y={noteY}
              opacity={noteOpacity}
              fill={style.tagText}
              fontSize={10.5}
              textAnchor={layer.centered ? "middle" : "start"}
              className="mono"
            >
              {layer.note}
            </motion.text>
          ) : null}

          <motion.text
            x={tagX}
            y={tagY}
            opacity={tagOpacity}
            fill={style.tagText}
            fontSize={9}
            letterSpacing="0.14em"
            textAnchor="end"
            className="mono"
          >
            {layer.tag}
          </motion.text>
        </>
      ) : null}

      {active ? (
        <motion.rect
          x={bracketX}
          y={bracketY}
          width={bracketW}
          height={bracketH}
          fill="none"
          stroke={style.stroke}
          strokeWidth={1}
          strokeDasharray="6 5"
          opacity={0.9}
        />
      ) : null}
    </g>
  );
}

function Connector({
  spec,
  progress,
  dimmed,
  active,
  uid,
}: {
  spec: ConnectorSpec;
  progress: MotionValue<number>;
  dimmed: boolean;
  active: boolean;
  uid: string;
}) {
  const d = useTransform(progress, (p) => connectorPath(spec, p));
  const stroke = spec.withheld ? "#ff7a29" : active ? "#4d7cff" : "#434852";
  const marker = spec.withheld ? "signal" : active ? "cobalt" : "rule";

  return (
    <motion.path
      className="transition-opacity duration-300 motion-reduce:transition-none"
      d={d}
      fill="none"
      stroke={stroke}
      strokeWidth={active ? 1.8 : 1.1}
      strokeDasharray={spec.withheld ? "5 4" : undefined}
      markerEnd={`url(#${uid}-arrow-${marker})`}
      opacity={dimmed ? 0.4 : 1}
    />
  );
}

function Barrier({
  progress,
  dimmed,
  active,
  showText,
  uid,
}: {
  progress: MotionValue<number>;
  dimmed: boolean;
  active: boolean;
  showText: boolean;
  uid: string;
}) {
  const x = useTransform(progress, (p) => lerpRect(BARRIER.a, BARRIER.e, p).x);
  const y = useTransform(progress, (p) => lerpRect(BARRIER.a, BARRIER.e, p).y);
  const w = useTransform(progress, (p) => lerpRect(BARRIER.a, BARRIER.e, p).w);
  const h = useTransform(progress, (p) => lerpRect(BARRIER.a, BARRIER.e, p).h);
  const labelX = useTransform(x, (v) => v + 2);
  const labelY = useTransform(y, (v) => v - 9);
  const labelOpacity = useTransform(progress, (p) => EASE_IN_RANGE(p, 0.5, 0.85));
  const zoneY = useTransform(y, (v) => v + 0);
  const zoneH = useTransform(y, (v) => Math.max(0, VIEWBOX.height - v));

  return (
    <g
      className="transition-opacity duration-300 motion-reduce:transition-none"
      opacity={dimmed ? 0.5 : 1}
    >
      <motion.rect x={0} y={zoneY} width={VIEWBOX.width} height={zoneH} fill="#0e0d0c" opacity={0.85} />
      <motion.rect x={x} y={y} width={w} height={h} fill={`url(#${uid}-hatch)`} />
      <motion.rect
        x={x}
        y={y}
        width={w}
        height={h}
        fill="none"
        stroke="#ff7a29"
        strokeWidth={active ? 2 : 1}
      />
      {showText ? (
        <motion.text
          x={labelX}
          y={labelY}
          opacity={labelOpacity}
          fill="#ff9a58"
          fontSize={9}
          letterSpacing="0.16em"
          className="mono"
        >
          HIDDEN-TEST BARRIER — NOTHING CROSSES UNTIL ALL 9 REVIEWS ARE RECORDED
        </motion.text>
      ) : null}
    </g>
  );
}

export interface ExplodedSystemProps {
  progress: MotionValue<number>;
  /** Layer ids currently highlighted; empty means "no emphasis". */
  activeLayers?: string[];
  onSelectLayer?: (id: string) => void;
  /** Compact, non-interactive rendering for the hero. */
  emblem?: boolean;
  className?: string;
}

export function ExplodedSystem({
  progress,
  activeLayers = [],
  onSelectLayer,
  emblem = false,
  className,
}: ExplodedSystemProps) {
  const hasFocus = activeLayers.length > 0;
  const isActive = React.useCallback(
    (ids: string[]) => ids.some((id) => activeLayers.includes(id)),
    [activeLayers],
  );
  // The hero emblem and the methodology diagram are two instances of
  // this SVG on one page; their pattern/marker ids must not collide.
  const uid = `vg${React.useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  // The hero emblem crops to the assembled stack and drops all
  // in-diagram type: at that size the labels would be unreadable noise,
  // and the labelled version lives one link away in `#method`.
  const viewBox = emblem ? "76 176 488 364" : `0 0 ${VIEWBOX.width} ${VIEWBOX.height}`;

  return (
    <svg viewBox={viewBox} className={className} aria-hidden="true" focusable="false">
      <defs>
        <pattern id={`${uid}-hatch`} width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
          <rect width="8" height="8" fill="#1a1109" />
          <line x1="0" y1="0" x2="0" y2="8" stroke="#ff7a29" strokeWidth="2" opacity="0.55" />
        </pattern>
        <marker id={`${uid}-arrow-rule`} markerWidth="7" markerHeight="7" refX="5.4" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill="#434852" />
        </marker>
        <marker id={`${uid}-arrow-cobalt`} markerWidth="7" markerHeight="7" refX="5.4" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill="#4d7cff" />
        </marker>
        <marker id={`${uid}-arrow-signal`} markerWidth="7" markerHeight="7" refX="5.4" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill="#ff7a29" />
        </marker>
      </defs>

      {CONNECTORS.map((spec) => (
        <Connector
          key={spec.id}
          spec={spec}
          progress={progress}
          dimmed={hasFocus && !isActive(spec.owners)}
          active={isActive(spec.owners)}
          uid={uid}
        />
      ))}

      <Barrier
        progress={progress}
        dimmed={hasFocus && !activeLayers.includes("barrier")}
        active={activeLayers.includes("barrier")}
        showText={!emblem}
        uid={uid}
      />

      {LAYERS.map((layer) => (
        <Plate
          key={layer.id}
          layer={layer}
          progress={progress}
          dimmed={hasFocus && !activeLayers.includes(layer.id)}
          active={activeLayers.includes(layer.id)}
          showText={!emblem}
          onSelect={emblem ? undefined : onSelectLayer}
        />
      ))}
    </svg>
  );
}
