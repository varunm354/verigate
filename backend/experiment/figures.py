"""Deterministic public-figure generation for VeriGate's Milestone 12 release.

This module reads **only** the sanitized, already-committed analysis output
under ``research/results/<campaign_id>/`` -- produced by
``experiment.campaign_analyzer.CampaignAnalyzer`` (Milestone 11) -- and
writes publication-quality PNG + SVG charts under ``research/figures/``.

It deliberately never:

- reads anything under ``backend/data/`` (raw campaign/candidate/experiment
  artifacts, which may be large, non-deterministic, or simply not present
  on a fresh checkout);
- initializes a reviewer/generator provider or makes a network call;
- writes to, or otherwise mutates, any research/results/ or
  research/adjudications.json file.

Three charts are produced (each as both a ``.png`` and a ``.svg``):

- ``condition_confidence`` -- mean A/B/C reviewer confidence for the
  4-candidate eligible set (package_resolver-only, produced under the
  transparently disclosed post-data adjudication amendment) vs. the
  complete 12-candidate descriptive cohort.
- ``candidate_effects`` -- per-candidate B-A and C-B effects for all 12
  qualifying candidates (short public-safe labels ``Q0``-``Q11``),
  distinguishing task and primary-analysis eligibility.
- ``adjudication_breakdown`` -- a horizontal bar chart of how many of the
  12 candidates fall into each adjudication category.

Determinism
-----------

Two consecutive calls to :func:`generate_all_figures` against unchanged
input files write byte-identical output (see
``backend/tests/test_figures.py::test_generate_all_figures_is_deterministic``).
This relies on:

- a fixed, headless ``Agg`` backend (never a GUI backend, never system
  fonts that might differ across machines -- only matplotlib's bundled
  DejaVu Sans);
- a fixed ``svg.hashsalt`` (matplotlib's SVG backend otherwise embeds
  memory-address-derived ids for clip paths, which vary run to run);
- fixed PNG/SVG metadata with no timestamp, no absolute path, no API
  identifier, and no hidden-test content -- see ``_PNG_METADATA`` /
  ``_SVG_METADATA`` below;
- a fixed draw order and fixed data ordering (ascending
  ``qualification_index``, a fixed A/B/C condition order, and a fixed
  adjudication-category order) instead of anything sorted by value;
- stripping trailing whitespace (spaces/tabs) from every line of the
  generated SVG text before hashing/writing it -- matplotlib's SVG writer
  is otherwise not guaranteed to avoid it, and trailing whitespace is an
  easy source of spurious diffs/hash drift with no visual effect.
"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Must be set before ``matplotlib`` (or anything that imports it) runs, and
# must never point outside this repository's own gitignored data directory
# -- this is local build-cache housekeeping only, never a data source.
os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(__file__).resolve().parent.parent / "data" / ".mplcache")
)

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from .campaign_store import repo_root  # noqa: E402

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Fixed, stable A/B/C order -- never sorted by value.
CONDITIONS: tuple[str, ...] = ("A_NO_RESULT", "B_VISIBLE_PASS", "C_ADVERSARIAL")

#: Fixed, stable adjudication-category order -- never sorted by count.
ADJUDICATION_CATEGORIES: tuple[str, ...] = (
    "valid_pass",
    "valid_failure",
    "benchmark_mismatch",
    "ambiguous",
)

#: The two tasks in campaign 8449581e-4097-4865-bfae-5cd8e9aef83f. Any other
#: task_id encountered in candidate_results.csv is a data-shape error.
TASK_IDS: tuple[str, ...] = ("json_parser", "package_resolver")

_REQUIRED_CANDIDATE_COUNT = 12

# Okabe-Ito colorblind-safe palette (https://jfly.uni-koeln.de/color/).
_COLOR_JSON_PARSER = "#0072B2"  # blue
_COLOR_PACKAGE_RESOLVER = "#E69F00"  # orange
_COLOR_ELIGIBLE_SET = "#009E73"  # bluish green
_COLOR_FULL_COHORT = "#56B4E9"  # sky blue
_COLOR_ZERO_LINE = "#404040"
_COLOR_EXCLUDED_NEUTRAL = "#8C8C8C"

_FONT_FAMILY = "DejaVu Sans"  # bundled with matplotlib; no system-font dependency
_DPI = 200

# No timestamp, no absolute path, no API/response id, no hidden-test
# content -- see module docstring "Determinism".
_PNG_METADATA = {"Software": "VeriGate research figure generator"}
_SVG_METADATA = {"Date": None, "Creator": "VeriGate research figure generator"}

_CONDITION_AXIS_LABELS = {
    "A_NO_RESULT": "A\n(no result)",
    "B_VISIBLE_PASS": "B\n(visible pass)",
    "C_ADVERSARIAL": "C\n(adversarial)",
}

_ADJUDICATION_AXIS_LABELS = {
    "valid_pass": "valid_pass\n(eligible)",
    "valid_failure": "valid_failure\n(eligible)",
    "benchmark_mismatch": "benchmark_mismatch\n(excluded)",
    "ambiguous": "ambiguous\n(excluded)",
}


class FigureDataError(RuntimeError):
    """Raised when a sanitized research/results file doesn't match the expected shape.

    Every message on this type is safe to print: it may reference a CSV
    column, task id, or count, but never hidden-test content, a secret, or
    an absolute filesystem path outside the repository.
    """


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def default_results_dir() -> Path:
    """``research/results``, resolved relative to this file."""

    return repo_root() / "research" / "results"


def default_figures_dir() -> Path:
    """``research/figures``, resolved relative to this file."""

    return repo_root() / "research" / "figures"


def discover_campaign_results_dir(results_dir: Path) -> Path:
    """Find the single campaign directory under ``results_dir``.

    Deliberately does not accept or require a campaign id in code (that
    would hardcode a value that belongs only in the data): it looks for
    exactly one subdirectory containing a ``candidate_results.csv``. More
    than one, or none, is a clear, safe error rather than a silent guess.
    """

    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        raise FigureDataError(f"results directory not found: {results_dir.name}")

    campaign_dirs = sorted(
        p for p in results_dir.iterdir() if p.is_dir() and (p / "candidate_results.csv").is_file()
    )
    if len(campaign_dirs) != 1:
        raise FigureDataError(
            "expected exactly one campaign directory containing candidate_results.csv under "
            f"research/results/, found {len(campaign_dirs)}: {[p.name for p in campaign_dirs]}"
        )
    return campaign_dirs[0]


# --------------------------------------------------------------------------
# Data loading (source-data validation lives here, not in the renderers)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateEffect:
    """One qualifying candidate's task, primary-analysis effects, and eligibility.

    Sourced entirely from one row of ``candidate_results.csv``.
    """

    qualification_index: int
    task_id: str
    b_minus_a: float
    c_minus_b: float
    eligible: bool
    adjudication_status: str

    @property
    def label(self) -> str:
        """Short, public-safe candidate label (never a raw UUID)."""

        return f"Q{self.qualification_index}"


@dataclass(frozen=True)
class ConditionScopeSummary:
    """One scope's (``full_cohort`` or ``primary_eligible``) overall A/B/C means.

    Sourced entirely from the ``task_id == "ALL"`` rows of
    ``condition_summary.csv``.
    """

    scope: str
    candidate_count: int
    mean_confidence: dict[str, float]


def load_candidate_effects(candidate_results_csv: Path) -> list[CandidateEffect]:
    """Load and validate every row of ``candidate_results.csv``.

    Raises :class:`FigureDataError` if the file does not describe exactly
    12 candidates with a contiguous ``0..11`` ``qualification_index`` and
    only the two known task ids -- the exact invariants
    ``experiment.campaign_analyzer`` already enforces when it wrote this
    file, checked again here defensively before anything is plotted.
    """

    candidate_results_csv = Path(candidate_results_csv)
    rows: list[CandidateEffect] = []
    try:
        with candidate_results_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                try:
                    rows.append(
                        CandidateEffect(
                            qualification_index=int(raw["qualification_index"]),
                            task_id=raw["task_id"],
                            b_minus_a=float(raw["b_minus_a"]),
                            c_minus_b=float(raw["c_minus_b"]),
                            eligible=raw["eligible_for_primary_analysis"].strip().lower() == "true",
                            adjudication_status=raw["adjudication_status"],
                        )
                    )
                except (KeyError, ValueError) as exc:
                    raise FigureDataError(
                        f"{candidate_results_csv.name}: malformed row {raw!r}: {exc}"
                    ) from exc
    except OSError as exc:
        raise FigureDataError(f"could not read {candidate_results_csv.name}: {exc}") from exc

    rows.sort(key=lambda r: r.qualification_index)

    if len(rows) != _REQUIRED_CANDIDATE_COUNT:
        raise FigureDataError(
            f"{candidate_results_csv.name}: expected exactly {_REQUIRED_CANDIDATE_COUNT} candidate "
            f"rows, found {len(rows)}"
        )
    expected_indices = list(range(_REQUIRED_CANDIDATE_COUNT))
    if [r.qualification_index for r in rows] != expected_indices:
        raise FigureDataError(
            f"{candidate_results_csv.name}: qualification_index values must be exactly "
            f"0..{_REQUIRED_CANDIDATE_COUNT - 1} with no gaps or duplicates"
        )
    unknown_tasks = sorted({r.task_id for r in rows} - set(TASK_IDS))
    if unknown_tasks:
        raise FigureDataError(f"{candidate_results_csv.name}: unknown task_id(s) {unknown_tasks}")
    unknown_statuses = sorted({r.adjudication_status for r in rows} - set(ADJUDICATION_CATEGORIES))
    if unknown_statuses:
        raise FigureDataError(
            f"{candidate_results_csv.name}: unknown adjudication_status value(s) {unknown_statuses}"
        )
    return rows


def load_condition_scope_summaries(condition_summary_csv: Path) -> dict[str, ConditionScopeSummary]:
    """Load the two overall (``task_id == "ALL"``) scope rows of ``condition_summary.csv``.

    Returns a dict with exactly the keys ``"full_cohort"`` and
    ``"primary_eligible"``. Raises :class:`FigureDataError` if either scope
    is missing any of the three A/B/C conditions or has no recorded mean
    confidence (which would happen only for an empty scope, never expected
    for the overall row).
    """

    condition_summary_csv = Path(condition_summary_csv)
    means: dict[str, dict[str, float]] = {"full_cohort": {}, "primary_eligible": {}}
    counts: dict[str, int] = {}
    try:
        with condition_summary_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                if raw["task_id"] != "ALL":
                    continue
                scope = raw["scope"]
                if scope not in means:
                    continue
                condition = raw["condition"]
                if condition not in CONDITIONS:
                    raise FigureDataError(
                        f"{condition_summary_csv.name}: unexpected condition {condition!r}"
                    )
                raw_mean = raw["mean_confidence"]
                if raw_mean in ("", None):
                    raise FigureDataError(
                        f"{condition_summary_csv.name}: missing mean_confidence for "
                        f"scope={scope!r} condition={condition!r}"
                    )
                means[scope][condition] = float(raw_mean)
                counts[scope] = int(raw["candidate_count"])
    except OSError as exc:
        raise FigureDataError(f"could not read {condition_summary_csv.name}: {exc}") from exc

    summaries: dict[str, ConditionScopeSummary] = {}
    for scope in ("full_cohort", "primary_eligible"):
        missing = set(CONDITIONS) - set(means[scope])
        if missing:
            raise FigureDataError(
                f"{condition_summary_csv.name}: scope={scope!r} is missing condition(s) "
                f"{sorted(missing)}"
            )
        summaries[scope] = ConditionScopeSummary(
            scope=scope, candidate_count=counts[scope], mean_confidence=dict(means[scope])
        )
    return summaries


def adjudication_counts(rows: list[CandidateEffect]) -> dict[str, int]:
    """Count each candidate's ``adjudication_status`` from already-loaded rows.

    Always returns every category in :data:`ADJUDICATION_CATEGORIES` (with
    a count of ``0`` if absent), in that fixed order -- never omitting a
    zero-count category, since the adjudication-breakdown chart must show
    it explicitly.
    """

    counts: dict[str, int] = {category: 0 for category in ADJUDICATION_CATEGORIES}
    for row in rows:
        counts[row.adjudication_status] += 1
    if sum(counts.values()) != len(rows):
        raise FigureDataError(  # pragma: no cover - load_candidate_effects already validates this
            "adjudication counts do not sum to the loaded candidate count"
        )
    return counts


@dataclass(frozen=True)
class CampaignFigureData:
    """Everything the three chart renderers need, loaded once and validated."""

    campaign_dir: Path
    candidate_effects: tuple[CandidateEffect, ...]
    condition_summaries: dict[str, ConditionScopeSummary]
    adjudication_counts: dict[str, int]


def load_campaign_figure_data(results_dir: Optional[Path] = None) -> CampaignFigureData:
    """Discover the campaign directory and load+validate every source file it needs."""

    resolved_results_dir = Path(results_dir) if results_dir is not None else default_results_dir()
    campaign_dir = discover_campaign_results_dir(resolved_results_dir)
    rows = load_candidate_effects(campaign_dir / "candidate_results.csv")
    summaries = load_condition_scope_summaries(campaign_dir / "condition_summary.csv")
    counts = adjudication_counts(rows)
    return CampaignFigureData(
        campaign_dir=campaign_dir,
        candidate_effects=tuple(rows),
        condition_summaries=summaries,
        adjudication_counts=counts,
    )


# --------------------------------------------------------------------------
# Shared style
# --------------------------------------------------------------------------


def _apply_style() -> None:
    """Consistent typography/visual style, applied before every render."""

    matplotlib.rcParams.update(
        {
            "font.family": _FONT_FAMILY,
            # Sized generously: GitHub renders README images at a fixed
            # display width, scaling this figure's pixels down substantially
            # -- text must stay legible after that shrink, not just at
            # native resolution.
            "font.size": 14,
            "axes.titlesize": 16,
            "axes.labelsize": 14,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 12,
            "axes.edgecolor": "#333333",
            "axes.linewidth": 0.8,
            "axes.grid": True,
            "grid.color": "#DDDDDD",
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.axisbelow": True,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "text.color": "#1A1A1A",
            "axes.labelcolor": "#1A1A1A",
            "xtick.color": "#1A1A1A",
            "ytick.color": "#1A1A1A",
            # Fixes SVG ids that would otherwise be derived from
            # object memory addresses (non-deterministic across runs).
            "svg.hashsalt": "verigate-research-figures-v1",
        }
    )


# --------------------------------------------------------------------------
# Chart A: condition_confidence
# --------------------------------------------------------------------------


def render_condition_confidence(summaries: dict[str, ConditionScopeSummary]) -> Figure:
    """Mean A/B/C confidence: 4-candidate eligible set vs. 12-candidate cohort.

    Source fields: ``condition_summary.csv`` rows with
    ``task_id=ALL`` for ``scope=primary_eligible`` (n=4, package_resolver
    only) and ``scope=full_cohort`` (n=12, all candidates).
    """

    _apply_style()
    eligible = summaries["primary_eligible"]
    full = summaries["full_cohort"]

    fig, ax = plt.subplots(figsize=(9.6, 6.6))
    positions = range(len(CONDITIONS))
    width = 0.34

    eligible_values = [eligible.mean_confidence[c] for c in CONDITIONS]
    full_values = [full.mean_confidence[c] for c in CONDITIONS]

    bars_eligible = ax.bar(
        [p - width / 2 for p in positions],
        eligible_values,
        width,
        label=f"Eligible set (n={eligible.candidate_count}, package_resolver only)",
        color=_COLOR_ELIGIBLE_SET,
        edgecolor="black",
        linewidth=0.8,
    )
    bars_full = ax.bar(
        [p + width / 2 for p in positions],
        full_values,
        width,
        label=f"Complete descriptive cohort (n={full.candidate_count}, all candidates)",
        color=_COLOR_FULL_COHORT,
        edgecolor="black",
        linewidth=0.8,
    )

    for bars, values in ((bars_eligible, eligible_values), (bars_full, full_values)):
        for rect, value in zip(bars, values):
            ax.annotate(
                f"{value:.1f}",
                (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=12,
                fontweight="bold",
            )

    ax.set_xticks(list(positions))
    ax.set_xticklabels([_CONDITION_AXIS_LABELS[c] for c in CONDITIONS])
    ax.set_ylabel("Mean reviewer confidence (0-100)")
    ax.set_ylim(0, 100)
    ax.set_title(
        "Mean A/B/C reviewer confidence: eligible set vs. complete cohort",
        fontsize=15,
        fontweight="bold",
        pad=14,
    )
    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="lower center",
        ncol=1,
        frameon=False,
        bbox_to_anchor=(0.5, 0.155),
    )

    fig.text(
        0.5,
        0.01,
        "The n=4 eligible set is package_resolver-only, produced under the transparently disclosed\n"
        "post-data adjudication amendment (docs/research_protocol.md, Amendment 1) -- it is not a\n"
        "preregistered primary analysis. The n=12 cohort is a purely descriptive view of every\n"
        "qualifying candidate and does not depend on that amendment.",
        ha="center",
        va="bottom",
        fontsize=10.5,
        color="#444444",
        style="italic",
    )
    fig.subplots_adjust(top=0.91, bottom=0.36, left=0.10, right=0.97)
    return fig


# --------------------------------------------------------------------------
# Chart B: candidate_effects
# --------------------------------------------------------------------------


def render_candidate_effects(rows: list[CandidateEffect]) -> Figure:
    """Per-candidate B-A and C-B effects for all 12 qualifying candidates.

    Source fields: ``candidate_results.csv`` columns ``qualification_index``,
    ``task_id``, ``b_minus_a``, ``c_minus_b``, ``eligible_for_primary_analysis``,
    ordered by ``qualification_index`` (Q0-Q11, not by value).
    """

    _apply_style()
    labels = [r.label for r in rows]
    positions = list(range(len(rows)))

    fig, axes = plt.subplots(2, 1, figsize=(10.0, 6.8), sharex=True)
    metrics = (
        ("b_minus_a", "B \u2212 A (visible-pass effect)"),
        ("c_minus_b", "C \u2212 B (adversarial-review effect)"),
    )

    for ax, (attr, subtitle) in zip(axes, metrics):
        values = [getattr(r, attr) for r in rows]
        colors = [
            _COLOR_JSON_PARSER if r.task_id == "json_parser" else _COLOR_PACKAGE_RESOLVER
            for r in rows
        ]
        edgecolors = ["black" if r.eligible else "#666666" for r in rows]
        linewidths = [1.4 if r.eligible else 0.7 for r in rows]

        bars = ax.bar(positions, values, color=colors, edgecolor=edgecolors, linewidth=linewidths)
        for bar, row in zip(bars, rows):
            if row.eligible:
                bar.set_hatch("///")

        ax.axhline(0, color=_COLOR_ZERO_LINE, linewidth=1.1, linestyle="--", zorder=0)
        ax.set_ylabel("Mean confidence\ndifference (points)")
        ax.set_title(subtitle, fontsize=14, loc="left")

    axes[-1].set_xticks(positions)
    axes[-1].set_xticklabels(labels)
    axes[-1].set_xlabel("Candidate (chronological qualification order)")

    task_handles = [
        mpatches.Patch(facecolor=_COLOR_JSON_PARSER, edgecolor="black", label="json_parser"),
        mpatches.Patch(facecolor=_COLOR_PACKAGE_RESOLVER, edgecolor="black", label="package_resolver"),
    ]
    n_eligible = sum(1 for r in rows if r.eligible)
    n_excluded = len(rows) - n_eligible
    eligibility_handles = [
        mpatches.Patch(
            facecolor="white",
            edgecolor="black",
            hatch="///",
            linewidth=1.4,
            label=f"Eligible for primary analysis (n={n_eligible})",
        ),
        mpatches.Patch(
            facecolor="white",
            edgecolor="#666666",
            linewidth=0.7,
            label=f"Excluded: benchmark_mismatch / ambiguous (n={n_excluded})",
        ),
    ]
    fig.legend(
        handles=task_handles + eligibility_handles,
        loc="lower center",
        ncol=2,
        frameon=False,
        fontsize=12,
        bbox_to_anchor=(0.5, 0.0),
    )

    fig.suptitle(
        "Per-candidate reviewer-confidence effects across all 12 qualifying candidates",
        fontsize=15,
        fontweight="bold",
    )
    fig.subplots_adjust(top=0.90, bottom=0.24, hspace=0.35, left=0.10, right=0.97)
    return fig


# --------------------------------------------------------------------------
# Chart C: adjudication_breakdown
# --------------------------------------------------------------------------


#: Top-to-bottom reading order for the horizontal bar chart (barh draws the
#: first entry at the bottom, so this is reversed just before plotting).
_ADJUDICATION_TOP_TO_BOTTOM = ("ambiguous", "benchmark_mismatch", "valid_failure", "valid_pass")


def render_adjudication_breakdown(counts: dict[str, int]) -> Figure:
    """Horizontal bar chart of adjudication-category counts across all 12 candidates.

    Source: ``adjudication_status`` column of ``candidate_results.csv``,
    counted per :data:`ADJUDICATION_CATEGORIES` (a fixed category order,
    never sorted by count) -- a labeled bar chart, deliberately not a pie
    chart, per Milestone 12's chart requirements.
    """

    _apply_style()
    categories = list(reversed(_ADJUDICATION_TOP_TO_BOTTOM))
    values = [counts[c] for c in categories]
    colors = [
        _COLOR_ELIGIBLE_SET if c in ("valid_pass", "valid_failure") else _COLOR_EXCLUDED_NEUTRAL
        for c in categories
    ]

    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    positions = range(len(categories))
    bars = ax.barh(list(positions), values, color=colors, edgecolor="black", linewidth=0.8)
    for rect, value in zip(bars, values):
        ax.annotate(
            str(value),
            (rect.get_width(), rect.get_y() + rect.get_height() / 2),
            xytext=(6, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=13,
            fontweight="bold",
        )

    ax.set_yticks(list(positions))
    ax.set_yticklabels([_ADJUDICATION_AXIS_LABELS[c] for c in categories])
    total = sum(values)
    ax.set_xlabel(f"Number of candidates (of {total})")
    ax.set_xlim(0, max(values + [1]) + 2)
    ax.set_title(
        "Adjudication outcomes across all 12 qualifying candidates",
        fontsize=15,
        fontweight="bold",
        pad=14,
    )
    ax.grid(axis="y", visible=False)
    fig.subplots_adjust(top=0.85, bottom=0.18, left=0.26, right=0.95)
    return fig


# --------------------------------------------------------------------------
# Saving (deterministic, atomic)
# --------------------------------------------------------------------------


def _save_figure(fig: Figure, output_dir: Path, stem: str) -> dict[str, str]:
    """Write ``<stem>.png`` and ``<stem>.svg`` atomically; return name -> sha256."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}

    for fmt, metadata in (("png", _PNG_METADATA), ("svg", _SVG_METADATA)):
        final_path = output_dir / f"{stem}.{fmt}"
        fd, tmp_name = tempfile.mkstemp(dir=str(output_dir), prefix=f".{final_path.name}.", suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            fig.savefig(tmp_path, format=fmt, dpi=_DPI, metadata=metadata)
            if fmt == "svg":
                # Deterministic SVG post-processing: strip trailing
                # spaces/tabs from every line. Purely cosmetic (no visual
                # or structural effect on the rendered image) but removes
                # a source of non-essential byte-level variation.
                text = tmp_path.read_text(encoding="utf-8")
                cleaned = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
                tmp_path.write_text(cleaned, encoding="utf-8")
            data = tmp_path.read_bytes()
            os.replace(tmp_path, final_path)
        except BaseException:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        hashes[final_path.name] = hashlib.sha256(data).hexdigest()
    return hashes


def generate_all_figures(
    *, results_dir: Optional[Path] = None, output_dir: Optional[Path] = None
) -> dict[str, str]:
    """Load the campaign's sanitized results, render, and save all three charts.

    Returns a dict of ``{"<stem>.png": sha256, "<stem>.svg": sha256, ...}``
    for all six output files, sorted by filename for a stable, readable
    return value. Never reads ``backend/data/``, never initializes a
    provider, never makes a network call.
    """

    data = load_campaign_figure_data(results_dir)
    resolved_output_dir = Path(output_dir) if output_dir is not None else default_figures_dir()

    hashes: dict[str, str] = {}
    renderers = (
        ("condition_confidence", lambda: render_condition_confidence(data.condition_summaries)),
        ("candidate_effects", lambda: render_candidate_effects(list(data.candidate_effects))),
        ("adjudication_breakdown", lambda: render_adjudication_breakdown(data.adjudication_counts)),
    )
    for stem, render in renderers:
        fig = render()
        try:
            hashes.update(_save_figure(fig, resolved_output_dir, stem))
        finally:
            plt.close(fig)

    return dict(sorted(hashes.items()))
