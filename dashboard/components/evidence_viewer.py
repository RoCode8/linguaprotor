"""Evidence Viewer — Reusable components for scoring evidence display.

Consumes the backend's ``ScoringResult`` shapes directly:
``criteria[].evidence[]`` (CriterionEvidence) and ``terminology_verdicts[]``
(TerminologyVerdict).
"""

from __future__ import annotations

from html import escape

import streamlit as st

_SEVERITY_STYLES: dict[str, dict[str, str]] = {
    "positive": {"color": "#2ecc71", "bg": "#eafaf1", "label": "Positive"},
    "minor": {"color": "#f39c12", "bg": "#fef9e7", "label": "Minor"},
    "major": {"color": "#e74c3c", "bg": "#fdedec", "label": "Major"},
    "critical": {"color": "#c0392b", "bg": "#f9ebea", "label": "Critical"},
    "unrated": {"color": "#95a5a6", "bg": "#f4f6f6", "label": "Note"},
}


def _evidence_style(item: dict) -> dict[str, str]:
    if item.get("is_positive"):
        return _SEVERITY_STYLES["positive"]
    return _SEVERITY_STYLES.get(item.get("severity") or "unrated", _SEVERITY_STYLES["unrated"])


def render_evidence(criteria: list[dict]) -> None:
    """Render one expander per criterion with its CriterionEvidence items.

    Args:
        criteria: ``ScoringResult.criteria`` — dicts with ``criterion``,
            ``summary`` and ``evidence`` (list of CriterionEvidence dicts).
    """
    for crit in criteria:
        items: list[dict] = crit.get("evidence") or []
        title = f"**{crit['criterion']}** ({len(items)} evidence item{'s' if len(items) != 1 else ''})"
        with st.expander(title):
            if crit.get("summary"):
                st.caption(crit["summary"])
            if not items:
                st.caption("No evidence items for this criterion.")
                continue
            for item in items:
                style = _evidence_style(item)
                issue = f"<strong>Issue:</strong> {escape(item['issue'])}<br/>" if item.get("issue") else ""
                candidate = item.get("candidate_span")
                st.markdown(
                    f"<div style='border-left:4px solid {style['color']}; background:{style['bg']}; "
                    f"padding:10px 14px; margin-bottom:8px; border-radius:4px; color:#222;'>"
                    f"<strong>{style['label']}</strong> "
                    f"<code>{escape(str(item.get('meaning_unit_id', '')))}</code><br/>"
                    f"<strong>Source:</strong> <code>{escape(item.get('source_span', ''))}</code><br/>"
                    f"<strong>Candidate:</strong> "
                    f"{'<code>' + escape(candidate) + '</code>' if candidate else '<em>(not rendered)</em>'}<br/>"
                    f"{issue}<em>{escape(item.get('explanation', ''))}</em>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


def _verdict_label(v: dict) -> str:
    if v.get("is_valid_alternative"):
        return "Valid alternative"
    return "Correct" if v.get("is_correct") else "Incorrect"


def render_terminology_table(verdicts: list[dict]) -> None:
    """Render ``ScoringResult.terminology_verdicts`` as a table."""
    if not verdicts:
        st.caption("No terminology verdicts (no glossary supplied or no terms found).")
        return

    st.dataframe(
        [
            {
                "Term": v["source_term"],
                "Expected": v["expected_translation"],
                "Candidate": v["candidate_translation"],
                "Verdict": _verdict_label(v),
                "Explanation": v.get("explanation", ""),
            }
            for v in verdicts
        ],
        width="stretch",
        hide_index=True,
    )

    alternatives = [v for v in verdicts if v.get("is_valid_alternative")]
    if alternatives:
        st.success("Valid alternatives accepted: " + ", ".join(f"**{a['source_term']}**" for a in alternatives))
