"""Score Heatmap — Reusable Plotly-based score visualisation components.

Provides gauge charts, criterion bar charts, and meaning-unit alignment maps.
Inputs are the backend's response shapes (CriterionScore / OralCriterionScore,
MeaningUnit, AlignedUnit).
"""

from __future__ import annotations

from html import escape

import plotly.graph_objects as go
import streamlit as st


def render_score_gauge(score: float, max_score: float, label: str) -> None:
    """Render a Plotly gauge for ``score`` out of ``max_score``."""
    max_score = max_score or 1.0
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": f" / {max_score:g}", "font": {"size": 22}},
            title={"text": label, "font": {"size": 14}},
            gauge={
                "axis": {"range": [0, max_score]},
                "bar": {"color": "#3498db"},
                "steps": [
                    {"range": [0, max_score * 0.4], "color": "#fdedec"},
                    {"range": [max_score * 0.4, max_score * 0.7], "color": "#fef9e7"},
                    {"range": [max_score * 0.7, max_score], "color": "#eafaf1"},
                ],
            },
        )
    )
    fig.update_layout(height=200, margin={"l": 20, "r": 20, "t": 40, "b": 10})
    st.plotly_chart(fig, width="stretch")


def _ratio(s: float, m: float) -> float:
    return s / m if m else 0.0


def render_criteria_chart(criteria: list[dict]) -> None:
    """Horizontal stacked bar chart of criterion scores.

    Args:
        criteria: dicts with ``criterion``, ``score`` and ``max_score``.
    """
    if not criteria:
        st.caption("No criterion scores returned.")
        return
    names = [c["criterion"] for c in criteria]
    scores = [float(c["score"]) for c in criteria]
    max_scores = [float(c["max_score"]) for c in criteria]
    colors = [
        "#2ecc71" if _ratio(s, m) >= 0.8 else "#f39c12" if _ratio(s, m) >= 0.6 else "#e74c3c"
        for s, m in zip(scores, max_scores)
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=names,
            x=scores,
            orientation="h",
            marker_color=colors,
            text=[f"{s:g}/{m:g}" for s, m in zip(scores, max_scores)],
            textposition="auto",
            name="Score",
        )
    )
    fig.add_trace(
        go.Bar(
            y=names,
            x=[max(m - s, 0) for s, m in zip(scores, max_scores)],
            orientation="h",
            marker_color="rgba(200,200,200,0.25)",
            name="Remaining",
        )
    )
    fig.update_layout(
        barmode="stack",
        height=max(200, 50 * len(names) + 60),
        xaxis_title="Score",
        showlegend=False,
        margin={"l": 0, "r": 20, "t": 10, "b": 30},
    )
    st.plotly_chart(fig, width="stretch")


def render_meaning_unit_map(meaning_units: list[dict], alignments: list[dict]) -> None:
    """Show each source meaning unit next to its aligned candidate text.

    Args:
        meaning_units: ``ScoringResult.meaning_units`` (MeaningUnit dicts).
        alignments: ``ScoringResult.alignments`` (AlignedUnit dicts with a
            nested ``meaning_unit``, ``candidate_text`` and ``alignment_confidence``).
    """
    by_id: dict[str, dict] = {a["meaning_unit"]["id"]: a for a in alignments if a.get("meaning_unit")}
    units = meaning_units or [a["meaning_unit"] for a in alignments]
    if not units:
        st.caption("No meaning units returned.")
        return

    for unit in units:
        uid = unit["id"]
        alignment = by_id.get(uid)
        candidate_text = (alignment or {}).get("candidate_text")
        conf = float((alignment or {}).get("alignment_confidence", 0.0))
        if not candidate_text:
            color, cand_html = "#e74c3c", "<em>missing</em>"
        else:
            color = "#2ecc71" if conf >= 0.85 else "#f39c12"
            cand_html = escape(candidate_text)

        st.markdown(
            f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:4px;'>"
            f"<code style='flex:0 0 60px; color:{color}; font-weight:bold;'>{escape(uid)}</code>"
            f"<span style='flex:2; font-family:serif;'>{escape(unit.get('source_text', ''))}</span>"
            f"<span style='flex:0 0 30px; text-align:center; color:{color};'>&rarr;</span>"
            f"<span style='flex:2; font-family:serif; direction:rtl;'>{cand_html}</span>"
            f"<span style='flex:0 0 50px; text-align:right;'>"
            f"<span style='background:{color}; color:white; padding:1px 6px; border-radius:3px; font-size:0.75em;'>{conf:.0%}</span>"
            f"</span></div>",
            unsafe_allow_html=True,
        )
