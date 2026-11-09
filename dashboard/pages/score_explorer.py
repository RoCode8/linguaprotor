"""Score Explorer — Score written translations and inspect the evidence.

Calls ``POST /api/v1/scoring/written`` (ScoringRequest -> ScoringResult) or
``GET /api/v1/scoring/{result_id}``, then renders criteria, meaning-unit
alignment, evidence, terminology verdicts and the HITL review panel.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_DASHBOARD_DIR = str(Path(__file__).resolve().parents[1])
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import streamlit as st  # noqa: E402

from components.api import api_request, demo_banner, is_demo_mode  # noqa: E402
from components.evidence_viewer import render_evidence, render_terminology_table  # noqa: E402
from components.mock_data import MOCK_SCORING_RESULT  # noqa: E402
from components.review_panel import render_review_panel  # noqa: E402
from components.score_heatmap import (  # noqa: E402
    render_criteria_chart,
    render_meaning_unit_map,
    render_score_gauge,
)

_RESULT_KEY = "score_explorer_result"

st.header("Score Explorer")
st.caption("Score translations, inspect evidence, and review verdicts.")
if is_demo_mode():
    demo_banner()


def _parse_glossary(text: str) -> tuple[list[dict], str | None]:
    """Parse ``source = target | alt1 | alt2`` lines into GlossaryTerm dicts."""
    entries: list[dict] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            return [], f"Glossary line {n} must look like `source term = target term | alternative ...`."
        src, rest = line.split("=", 1)
        parts = [p.strip() for p in rest.split("|") if p.strip()]
        if not src.strip() or not parts:
            return [], f"Glossary line {n} has an empty source or target term."
        entries.append({"source_term": src.strip(), "target_term": parts[0], "alternatives": parts[1:]})
    return entries, None


tab_adhoc, tab_lookup = st.tabs(["Ad-Hoc Scoring", "Look Up Result"])

with tab_adhoc:
    with st.form("score_form"):
        c1, c2, c3, c4 = st.columns(4)
        exam_id = c1.text_input("Exam ID", value="adhoc-exam")
        candidate_id = c2.text_input("Candidate ID", value="adhoc-candidate")
        source_language = c3.text_input("Source language", value="en")
        target_language = c4.text_input("Target language", value="ar")
        source_text = st.text_area(
            "Source Text",
            value=(
                "The parties hereby agree to submit any dispute arising out of or in connection with "
                "this contract to binding arbitration in accordance with the rules of the "
                "International Chamber of Commerce."
            ),
            height=100,
        )
        candidate_translation = st.text_area(
            "Candidate Translation",
            value="يتفق الأطراف بموجب هذا على إحالة أي نزاع ينشأ عن هذا العقد أو يتصل به إلى التحكيم الملزم وفقاً لقواعد غرفة التجارة الدولية.",
            height=100,
        )
        reference_translation = st.text_area("Reference Translation (optional)", height=80)
        glossary_text = st.text_area(
            "Terminology Glossary (optional, one per line: `source = target | alternative | ...`)",
            value="binding arbitration = التحكيم الملزم\ndispute = نزاع | خلاف",
            height=80,
        )
        rubric_version = st.text_input("Rubric version", value="v1")
        score_btn = st.form_submit_button("Score Translation", width="stretch", type="primary")

    if score_btn:
        glossary, gloss_err = _parse_glossary(glossary_text)
        if gloss_err:
            st.error(gloss_err)
        elif not source_text.strip() or not candidate_translation.strip():
            st.error("Source text and candidate translation are required.")
        elif is_demo_mode():
            st.session_state[_RESULT_KEY] = MOCK_SCORING_RESULT
        else:
            payload = {
                "exam_id": exam_id.strip() or f"adhoc-{uuid.uuid4().hex[:8]}",
                "candidate_id": candidate_id.strip() or "adhoc-candidate",
                "source_text": source_text,
                "candidate_translation": candidate_translation,
                "source_language": source_language.strip() or "en",
                "target_language": target_language.strip() or "ar",
                "reference_translation": reference_translation.strip() or None,
                "terminology_glossary": glossary,
                "rubric_version": rubric_version.strip() or "v1",
            }
            with st.spinner("Scoring translation…"):
                result, err = api_request("POST", "/scoring/written", json=payload)
            if err:
                st.session_state.pop(_RESULT_KEY, None)
                st.error(f"Scoring failed: {err}")
            else:
                st.session_state[_RESULT_KEY] = result

with tab_lookup:
    result_id = st.text_input("Scoring result ID", placeholder="UUID returned by /scoring/written")
    if st.button("Look Up", width="stretch", type="primary"):
        if is_demo_mode():
            st.session_state[_RESULT_KEY] = MOCK_SCORING_RESULT
        elif not result_id.strip():
            st.error("Enter a result ID.")
        else:
            result, err = api_request("GET", f"/scoring/{result_id.strip()}")
            if err:
                st.session_state.pop(_RESULT_KEY, None)
                st.error(f"Lookup failed: {err}")
            else:
                st.session_state[_RESULT_KEY] = result

data: dict | None = st.session_state.get(_RESULT_KEY)
if data is not None and data is MOCK_SCORING_RESULT and not is_demo_mode():
    # Demo result left over after the toggle was switched off: never show it as real.
    st.session_state.pop(_RESULT_KEY, None)
    data = None

if data:
    st.divider()
    st.caption(f"Result `{data.get('id')}` · exam `{data.get('exam_id')}` · candidate `{data.get('candidate_id')}`")

    metric_cols = st.columns(4)
    with metric_cols[0]:
        render_score_gauge(data["overall_score"], data["overall_max"], "Overall Score")
    metric_cols[1].metric("Percentage", f"{data.get('percentage', 0):.1f}%")
    metric_cols[2].metric("Confidence", f"{data['confidence']:.0%}")
    metric_cols[3].metric("Valid alternatives", str(data.get("valid_alternatives_detected", 0)))

    for flag in data.get("flags") or []:
        st.warning(f"Flag: {flag}")

    st.subheader("Criterion Breakdown")
    render_criteria_chart(data["criteria"])
    for crit in data["criteria"]:
        st.markdown(f"- **{crit['criterion']}** ({crit['score']:g}/{crit['max_score']:g}): {crit.get('summary', '')}")

    st.subheader("Meaning-Unit Alignment")
    render_meaning_unit_map(data.get("meaning_units") or [], data.get("alignments") or [])

    st.subheader("Evidence")
    render_evidence(data["criteria"])

    st.subheader("Terminology Verdicts")
    render_terminology_table(data.get("terminology_verdicts") or [])

    if data.get("scoring_metadata"):
        with st.expander("Scoring metadata"):
            st.json(data["scoring_metadata"])

    st.divider()
    render_review_panel(data["id"], "written", data["criteria"], key_prefix="written")
