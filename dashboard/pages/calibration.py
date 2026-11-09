"""Calibration Dashboard — AI-vs-human agreement metrics.

Calls ``GET /api/v1/review/calibration/report`` (CalibrationReport) and renders
agreement statistics, per-criterion mean AI-human difference and the
backend's recommendations.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DASHBOARD_DIR = str(Path(__file__).resolve().parents[1])
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from components.api import api_request, demo_banner, is_demo_mode  # noqa: E402
from components.mock_data import MOCK_CALIBRATION_REPORT  # noqa: E402


def _kappa_quality(k: float) -> str:
    # Landis & Koch (1977) bands
    if k < 0.0:
        return "Poor"
    for upper, label in ((0.20, "Slight"), (0.40, "Fair"), (0.60, "Moderate"), (0.80, "Substantial")):
        if k <= upper:
            return label
    return "Almost perfect"


st.header("Calibration Dashboard")
st.caption("AI scoring agreement with human expert judgement, computed by the backend from review outcomes.")
if is_demo_mode():
    demo_banner()

if st.button("Generate Report", type="primary"):
    if is_demo_mode():
        data, err = MOCK_CALIBRATION_REPORT, None
    else:
        with st.spinner("Generating calibration report…"):
            data, err = api_request("GET", "/review/calibration/report")

    if err:
        st.error(f"Could not load calibration report: {err}")
    elif data:
        st.caption(
            f"Report `{data.get('report_id')}` · period {data.get('period_start', '?')} → {data.get('period_end', '?')}"
        )
        st.divider()

        st.subheader("Volume")
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Total assessments", data["total_assessments"])
        v2.metric("Human reviewed", data.get("human_reviewed", 0))
        v3.metric("Overrides", data["overrides"])
        v4.metric("Override rate", f"{data['override_rate']:.1%}")

        st.subheader("Agreement")
        kappa, icc = data.get("cohens_kappa"), data.get("icc")
        a1, a2, a3 = st.columns(3)
        a1.metric("Cohen's kappa", "n/a" if kappa is None else f"{kappa:.2f}")
        a2.metric("Kappa quality", "n/a" if kappa is None else _kappa_quality(kappa))
        a3.metric("ICC(2,1)", "n/a" if icc is None else f"{icc:.2f}")
        if kappa is None or icc is None:
            st.info("Agreement statistics are unavailable until enough human-reviewed assessments exist.")

        st.subheader("Mean AI − Human Difference per Criterion")
        diffs: dict[str, float] = data.get("mean_ai_human_difference") or {}
        if diffs:
            names = list(diffs)
            values = [diffs[n] for n in names]
            fig = go.Figure(
                go.Bar(
                    x=names,
                    y=values,
                    marker_color=["#e74c3c" if abs(v) > 0.5 else "#f39c12" if abs(v) > 0.2 else "#2ecc71" for v in values],
                    text=[f"{v:+.2f}" for v in values],
                    textposition="auto",
                )
            )
            fig.add_hline(y=0, line_dash="dash", line_color="grey")
            fig.update_layout(yaxis_title="AI − Human (points)", height=350)
            st.plotly_chart(fig, width="stretch")
            st.caption("Positive = AI scores higher than humans; negative = AI scores lower.")
        else:
            st.caption("No per-criterion differences yet (no human-reviewed assessments).")

        st.subheader("Recommendations")
        recs = data.get("recommendations") or []
        if recs:
            for i, rec in enumerate(recs, 1):
                st.markdown(f"{i}. {rec}")
        else:
            st.caption("No recommendations.")
