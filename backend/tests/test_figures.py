"""Tests for Milestone 12's deterministic public-figure generation.

Two kinds of coverage:

1. Source-data validation and core plotted values against synthetic,
   ``tmp_path``-only CSVs (fast, isolated, no dependency on real data ever
   changing shape).
2. The same loaders and a full ``generate_all_figures`` run against the
   real, committed ``research/results/`` output, asserting the exact
   canonical Milestone 12 values and full-run determinism.

No test makes a network/API call, and no test reads or writes anything
under ``backend/data/``.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from experiment.figures import (
    ADJUDICATION_CATEGORIES,
    CONDITIONS,
    CandidateEffect,
    FigureDataError,
    adjudication_counts,
    discover_campaign_results_dir,
    generate_all_figures,
    load_campaign_figure_data,
    load_candidate_effects,
    load_condition_scope_summaries,
    render_adjudication_breakdown,
    render_candidate_effects,
    render_condition_confidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_RESULTS_DIR = REPO_ROOT / "research" / "results"


# --------------------------------------------------------------------------
# Synthetic fixtures
# --------------------------------------------------------------------------

_CANDIDATE_FIELDNAMES = [
    "qualification_index",
    "task_id",
    "candidate_id",
    "b_minus_a",
    "c_minus_b",
    "adjudication_status",
    "eligible_for_primary_analysis",
]

_CONDITION_FIELDNAMES = [
    "scope",
    "task_id",
    "condition",
    "candidate_count",
    "mean_confidence",
    "predicted_pass_count",
    "predicted_pass_rate",
]


def _write_candidate_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CANDIDATE_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_condition_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CONDITION_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _synthetic_candidate_row(
    idx: int,
    task_id: str,
    *,
    b_minus_a: float = 1.0,
    c_minus_b: float = -1.0,
    status: str = "ambiguous",
    eligible: bool = False,
) -> dict:
    return {
        "qualification_index": idx,
        "task_id": task_id,
        "candidate_id": f"00000000-0000-0000-0000-{idx:012d}",
        "b_minus_a": b_minus_a,
        "c_minus_b": c_minus_b,
        "adjudication_status": status,
        "eligible_for_primary_analysis": str(eligible),
    }


def _synthetic_12_candidates() -> list[dict]:
    rows = []
    for idx in range(12):
        task_id = "json_parser" if idx % 2 == 0 else "package_resolver"
        status = "valid_failure" if idx in (3, 5, 7, 11) else (
            "benchmark_mismatch" if idx == 0 else "ambiguous"
        )
        eligible = status in ("valid_pass", "valid_failure")
        rows.append(
            _synthetic_candidate_row(
                idx, task_id, b_minus_a=float(idx), c_minus_b=-float(idx), status=status, eligible=eligible
            )
        )
    return rows


def _synthetic_condition_summary_rows() -> list[dict]:
    rows = []
    for scope, count in (("full_cohort", 12), ("primary_eligible", 4)):
        for condition, mean in zip(CONDITIONS, (50.0, 55.0, 45.0)):
            rows.append(
                {
                    "scope": scope,
                    "task_id": "ALL",
                    "condition": condition,
                    "candidate_count": count,
                    "mean_confidence": mean,
                    "predicted_pass_count": 1,
                    "predicted_pass_rate": 0.5,
                }
            )
        # Also add a per-task row, which loaders must ignore.
        rows.append(
            {
                "scope": scope,
                "task_id": "json_parser",
                "condition": CONDITIONS[0],
                "candidate_count": count,
                "mean_confidence": 999.0,
                "predicted_pass_count": 1,
                "predicted_pass_rate": 0.5,
            }
        )
    return rows


@pytest.fixture()
def synthetic_campaign_dir(tmp_path: Path) -> Path:
    campaign_dir = tmp_path / "results" / "synthetic-campaign"
    campaign_dir.mkdir(parents=True)
    _write_candidate_csv(campaign_dir / "candidate_results.csv", _synthetic_12_candidates())
    _write_condition_csv(campaign_dir / "condition_summary.csv", _synthetic_condition_summary_rows())
    return campaign_dir


# --------------------------------------------------------------------------
# Source-data validation (synthetic)
# --------------------------------------------------------------------------


def test_load_candidate_effects_happy_path(synthetic_campaign_dir: Path) -> None:
    rows = load_candidate_effects(synthetic_campaign_dir / "candidate_results.csv")
    assert len(rows) == 12
    assert [r.qualification_index for r in rows] == list(range(12))
    assert rows[3].eligible is True
    assert rows[3].label == "Q3"
    assert rows[0].task_id == "json_parser"
    assert rows[1].task_id == "package_resolver"


def test_load_candidate_effects_rejects_wrong_row_count(tmp_path: Path) -> None:
    csv_path = tmp_path / "candidate_results.csv"
    _write_candidate_csv(csv_path, _synthetic_12_candidates()[:11])
    with pytest.raises(FigureDataError, match="expected exactly 12"):
        load_candidate_effects(csv_path)


def test_load_candidate_effects_rejects_gap_in_qualification_index(tmp_path: Path) -> None:
    rows = _synthetic_12_candidates()
    rows[5]["qualification_index"] = 99  # creates a gap and a duplicate-free-but-wrong sequence
    csv_path = tmp_path / "candidate_results.csv"
    _write_candidate_csv(csv_path, rows)
    with pytest.raises(FigureDataError, match="0..11"):
        load_candidate_effects(csv_path)


def test_load_candidate_effects_rejects_unknown_task_id(tmp_path: Path) -> None:
    rows = _synthetic_12_candidates()
    rows[0]["task_id"] = "some_other_task"
    csv_path = tmp_path / "candidate_results.csv"
    _write_candidate_csv(csv_path, rows)
    with pytest.raises(FigureDataError, match="unknown task_id"):
        load_candidate_effects(csv_path)


def test_load_candidate_effects_rejects_unknown_adjudication_status(tmp_path: Path) -> None:
    rows = _synthetic_12_candidates()
    rows[0]["adjudication_status"] = "not_a_real_status"
    csv_path = tmp_path / "candidate_results.csv"
    _write_candidate_csv(csv_path, rows)
    with pytest.raises(FigureDataError, match="unknown adjudication_status"):
        load_candidate_effects(csv_path)


def test_load_condition_scope_summaries_happy_path(synthetic_campaign_dir: Path) -> None:
    summaries = load_condition_scope_summaries(synthetic_campaign_dir / "condition_summary.csv")
    assert set(summaries) == {"full_cohort", "primary_eligible"}
    assert summaries["full_cohort"].candidate_count == 12
    assert summaries["primary_eligible"].candidate_count == 4
    assert summaries["full_cohort"].mean_confidence["A_NO_RESULT"] == 50.0
    assert summaries["full_cohort"].mean_confidence["B_VISIBLE_PASS"] == 55.0
    assert summaries["full_cohort"].mean_confidence["C_ADVERSARIAL"] == 45.0
    # The per-task row (mean_confidence=999.0) must never leak into the
    # overall (task_id == "ALL") summary.
    assert 999.0 not in summaries["full_cohort"].mean_confidence.values()


def test_load_condition_scope_summaries_rejects_missing_condition(tmp_path: Path) -> None:
    rows = [r for r in _synthetic_condition_summary_rows() if not (r["task_id"] == "ALL" and r["condition"] == "C_ADVERSARIAL" and r["scope"] == "full_cohort")]
    csv_path = tmp_path / "condition_summary.csv"
    _write_condition_csv(csv_path, rows)
    with pytest.raises(FigureDataError, match="missing condition"):
        load_condition_scope_summaries(csv_path)


def test_adjudication_counts_includes_zero_categories(synthetic_campaign_dir: Path) -> None:
    rows = load_candidate_effects(synthetic_campaign_dir / "candidate_results.csv")
    counts = adjudication_counts(rows)
    assert set(counts) == set(ADJUDICATION_CATEGORIES)
    assert counts["valid_pass"] == 0  # present with count 0, not omitted
    assert sum(counts.values()) == 12


def test_discover_campaign_results_dir_requires_exactly_one(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    with pytest.raises(FigureDataError, match="found 0"):
        discover_campaign_results_dir(results_dir)

    (results_dir / "campaign-a").mkdir()
    (results_dir / "campaign-a" / "candidate_results.csv").write_text("x", encoding="utf-8")
    (results_dir / "campaign-b").mkdir()
    (results_dir / "campaign-b" / "candidate_results.csv").write_text("x", encoding="utf-8")
    with pytest.raises(FigureDataError, match="found 2"):
        discover_campaign_results_dir(results_dir)


# --------------------------------------------------------------------------
# Core plotted values (synthetic, introspecting the rendered Figure)
# --------------------------------------------------------------------------


def test_render_condition_confidence_bar_heights_match_source(synthetic_campaign_dir: Path) -> None:
    summaries = load_condition_scope_summaries(synthetic_campaign_dir / "condition_summary.csv")
    fig = render_condition_confidence(summaries)
    try:
        ax = fig.axes[0]
        heights = sorted(rect.get_height() for rect in ax.patches)
        expected = sorted(
            list(summaries["primary_eligible"].mean_confidence.values())
            + list(summaries["full_cohort"].mean_confidence.values())
        )
        assert heights == pytest.approx(expected)
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_render_candidate_effects_bar_heights_match_source(synthetic_campaign_dir: Path) -> None:
    rows = load_candidate_effects(synthetic_campaign_dir / "candidate_results.csv")
    fig = render_candidate_effects(rows)
    try:
        top_ax, bottom_ax = fig.axes[0], fig.axes[1]
        top_heights = [rect.get_height() for rect in top_ax.patches]
        bottom_heights = [rect.get_height() for rect in bottom_ax.patches]
        assert top_heights == pytest.approx([r.b_minus_a for r in rows])
        assert bottom_heights == pytest.approx([r.c_minus_b for r in rows])
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_render_adjudication_breakdown_bar_widths_match_source(synthetic_campaign_dir: Path) -> None:
    rows = load_candidate_effects(synthetic_campaign_dir / "candidate_results.csv")
    counts = adjudication_counts(rows)
    fig = render_adjudication_breakdown(counts)
    try:
        ax = fig.axes[0]
        widths = sorted(rect.get_width() for rect in ax.patches)
        assert widths == sorted(counts.values())
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


# --------------------------------------------------------------------------
# Real, committed research/results/ data: canonical Milestone 12 values
# --------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_RESULTS_DIR.is_dir(), reason="research/results/ not present in this checkout")
class TestRealCampaignData:
    def test_candidate_effects_canonical_shape(self) -> None:
        data = load_campaign_figure_data(REAL_RESULTS_DIR)
        rows = data.candidate_effects
        assert len(rows) == 12
        assert sum(1 for r in rows if r.eligible) == 4
        assert sum(1 for r in rows if r.task_id == "json_parser") == 6
        assert sum(1 for r in rows if r.task_id == "package_resolver") == 6
        # Every eligible candidate is package_resolver (per the post-data
        # adjudication amendment applied to this campaign).
        assert all(r.task_id == "package_resolver" for r in rows if r.eligible)

    def test_adjudication_counts_canonical_values(self) -> None:
        data = load_campaign_figure_data(REAL_RESULTS_DIR)
        assert data.adjudication_counts == {
            "valid_pass": 0,
            "valid_failure": 4,
            "benchmark_mismatch": 1,
            "ambiguous": 7,
        }

    def test_condition_confidence_canonical_values(self) -> None:
        data = load_campaign_figure_data(REAL_RESULTS_DIR)
        eligible = data.condition_summaries["primary_eligible"]
        full = data.condition_summaries["full_cohort"]

        assert eligible.candidate_count == 4
        assert full.candidate_count == 12

        assert eligible.mean_confidence["A_NO_RESULT"] == pytest.approx(47.750, abs=1e-3)
        assert eligible.mean_confidence["B_VISIBLE_PASS"] == pytest.approx(51.917, abs=1e-3)
        assert eligible.mean_confidence["C_ADVERSARIAL"] == pytest.approx(47.583, abs=1e-3)

        assert full.mean_confidence["A_NO_RESULT"] == pytest.approx(69.528, abs=1e-3)
        assert full.mean_confidence["B_VISIBLE_PASS"] == pytest.approx(68.639, abs=1e-3)
        assert full.mean_confidence["C_ADVERSARIAL"] == pytest.approx(67.583, abs=1e-3)

    def test_paired_differences_canonical_values(self) -> None:
        data = load_campaign_figure_data(REAL_RESULTS_DIR)
        eligible_rows = [r for r in data.candidate_effects if r.eligible]
        full_rows = list(data.candidate_effects)

        mean_b_minus_a_eligible = sum(r.b_minus_a for r in eligible_rows) / len(eligible_rows)
        mean_c_minus_b_eligible = sum(r.c_minus_b for r in eligible_rows) / len(eligible_rows)
        mean_b_minus_a_full = sum(r.b_minus_a for r in full_rows) / len(full_rows)
        mean_c_minus_b_full = sum(r.c_minus_b for r in full_rows) / len(full_rows)

        assert mean_b_minus_a_eligible == pytest.approx(4.167, abs=1e-3)
        assert mean_c_minus_b_eligible == pytest.approx(-4.333, abs=1e-3)
        assert mean_b_minus_a_full == pytest.approx(-0.889, abs=1e-3)
        assert mean_c_minus_b_full == pytest.approx(-1.056, abs=1e-3)

    def test_generate_all_figures_writes_six_files_with_canonical_hashes_stable(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "figures"
        first = generate_all_figures(results_dir=REAL_RESULTS_DIR, output_dir=output_dir)
        assert set(first) == {
            "adjudication_breakdown.png",
            "adjudication_breakdown.svg",
            "candidate_effects.png",
            "candidate_effects.svg",
            "condition_confidence.png",
            "condition_confidence.svg",
        }
        for name in first:
            assert (output_dir / name).is_file()

        # Regenerating into a fresh directory against the same unchanged
        # source files must be byte-identical (see module docstring).
        second_output_dir = tmp_path / "figures-rerun"
        second = generate_all_figures(results_dir=REAL_RESULTS_DIR, output_dir=second_output_dir)
        assert first == second

    def test_generated_figures_have_no_leaked_paths_or_timestamps(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "figures"
        generate_all_figures(results_dir=REAL_RESULTS_DIR, output_dir=output_dir)
        for svg_path in output_dir.glob("*.svg"):
            text = svg_path.read_text(encoding="utf-8")
            assert str(REPO_ROOT) not in text
            assert "/Users/" not in text
            assert "<dc:date>" not in text.lower()
