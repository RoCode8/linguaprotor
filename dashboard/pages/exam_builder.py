"""Exam Builder — Generate bilingual exam passages and manage expert review.

Endpoints:
- ``POST /api/v1/content/generate`` (ContentGenerationRequest -> list[GeneratedPassage], 201)
- ``POST /api/v1/content/{id}/validate`` -> ComprehensiveValidationResult
- ``POST /api/v1/content/{id}/review`` (ContentReviewFeedback) -> GeneratedPassage
- ``GET  /api/v1/content/queue/pending`` -> list[GeneratedPassage]
"""

from __future__ import annotations

import sys
from pathlib import Path

_DASHBOARD_DIR = str(Path(__file__).resolve().parents[1])
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import streamlit as st  # noqa: E402

from components.api import api_request, demo_banner, is_demo_mode  # noqa: E402
from components.mock_data import MOCK_PASSAGES, MOCK_VALIDATION  # noqa: E402

# Enum values from the OpenAPI spec (ExamDomain / ExamDifficulty).
DOMAINS: list[str] = ["legal", "medical", "technical", "general", "financial"]
DIFFICULTIES: list[str] = ["B2", "C1", "C2"]
GENERATE_TIMEOUT: float = 240.0
LANGUAGE_PAIRS: dict[str, tuple[str, str]] = {"English → Arabic": ("en", "ar"), "Arabic → English": ("ar", "en")}
REVIEW_DECISIONS: list[str] = ["approved", "revision_requested", "rejected"]

_GENERATED_KEY = "exam_builder_passages"

st.header("Exam Builder")
st.caption("Generate bilingual exam passages and manage the expert review pipeline.")
if is_demo_mode():
    demo_banner()


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _render_validation(v: dict) -> None:
    st.markdown(f"**Overall valid:** {'Yes' if v.get('overall_valid') else 'No'}")
    for name in ("difficulty", "terminology"):
        part = v.get(name) or {}
        st.markdown(f"- **{name.title()}**: {'pass' if part.get('is_valid') else 'fail'} (score {part.get('score', 0):.2f})")
        for issue in part.get("issues") or []:
            st.markdown(f"  - {issue}")
    dup = v.get("duplication") or {}
    st.markdown(
        f"- **Duplication**: {'DUPLICATE' if dup.get('is_duplicate') else 'unique'} "
        f"(max similarity {dup.get('max_similarity', 0):.2f})"
    )
    if dup.get("similar_passage_ids"):
        st.caption("Similar passages: " + ", ".join(dup["similar_passage_ids"]))


def render_passage(p: dict, key_prefix: str) -> None:
    """Render one GeneratedPassage plus validate / review actions."""
    pid = p.get("id", "?")
    c = st.columns(4)
    c[0].metric("Domain", p.get("domain", "?"))
    c[1].metric("Difficulty", p.get("difficulty", "?"))
    c[2].metric("CEFR", p.get("cefr_level", "?"))
    c[3].metric("Words", p.get("word_count", 0))
    st.caption(f"ID `{pid}` · {p.get('source_language')} → {p.get('target_language')} · status `{p.get('review_status')}`")

    st.markdown("##### Source Text")
    st.markdown(f"> {p['source_text']}")

    ma = p.get("model_answer") or {}
    with st.expander("Model Answer"):
        st.markdown(ma.get("translation") or "_(none)_")
        if ma.get("translation_notes"):
            st.caption(f"Notes: {ma['translation_notes']}")
        if ma.get("difficulty_justification"):
            st.caption(f"Difficulty justification: {ma['difficulty_justification']}")

    glossary = p.get("terminology_glossary") or []
    st.markdown("##### Terminology Glossary")
    if glossary:
        st.dataframe(
            [
                {
                    "Source term": g["source_term"],
                    "Target term": g["target_term"],
                    "Alternatives": ", ".join(g.get("alternatives") or []),
                    "Domain": g.get("domain", ""),
                    "Notes": g.get("notes") or "",
                }
                for g in glossary
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No glossary returned.")

    if st.button("Run validation checks", key=f"{key_prefix}_validate_{pid}"):
        if is_demo_mode():
            v, err = MOCK_VALIDATION, None
        else:
            with st.spinner("Validating…"):
                v, err = api_request("POST", f"/content/{pid}/validate")
        if err:
            st.error(f"Validation failed: {err}")
        else:
            _render_validation(v)

    with st.form(f"{key_prefix}_review_{pid}"):
        st.markdown("##### Expert Review")
        r1, r2 = st.columns(2)
        reviewer_id = r1.text_input("Reviewer ID", key=f"{key_prefix}_rid_{pid}")
        decision = r2.selectbox("Decision", REVIEW_DECISIONS, key=f"{key_prefix}_dec_{pid}")
        comments = st.text_area("Comments", key=f"{key_prefix}_com_{pid}")
        if st.form_submit_button("Submit review"):
            if not reviewer_id.strip() or not comments.strip():
                st.error("Reviewer ID and comments are required.")
            elif is_demo_mode():
                st.info("Demo mode: review was NOT sent to the API.")
            else:
                body = {"passage_id": pid, "reviewer_id": reviewer_id.strip(), "decision": decision, "comments": comments.strip()}
                updated, err = api_request("POST", f"/content/{pid}/review", json=body)
                if err:
                    st.error(f"Review submission failed: {err}")
                else:
                    st.success(f"Review recorded. Passage status: {updated.get('review_status')}")


tab_generate, tab_queue = st.tabs(["Generate Passages", "Pending Review Queue"])

with tab_generate:
    with st.form("generate_form"):
        col1, col2, col3 = st.columns(3)
        domain = col1.selectbox("Domain", DOMAINS)
        difficulty = col2.selectbox("Difficulty (CEFR)", DIFFICULTIES, index=1)
        lang_pair = col3.selectbox("Language Pair", list(LANGUAGE_PAIRS))
        col4, col5 = st.columns([1, 3])
        num_passages = col4.number_input("Number of passages", min_value=1, max_value=10, value=1)
        include_model_answer = col4.checkbox("Include model answer", value=True)
        reference_texts = col5.text_area(
            "Reference texts (optional, one per line)",
            placeholder="Exemplar passages to guide style/content…",
            height=80,
        )
        specific_terminology = col5.text_area("Required terminology (optional, one per line)", height=60)
        submitted = st.form_submit_button("Generate", width="stretch", type="primary")

    if submitted:
        if is_demo_mode():
            st.session_state[_GENERATED_KEY] = MOCK_PASSAGES
        else:
            src, tgt = LANGUAGE_PAIRS[lang_pair]
            payload = {
                "source_language": src,
                "target_language": tgt,
                "domain": domain,
                "difficulty": difficulty,
                "reference_texts": _split_lines(reference_texts),
                "num_passages": int(num_passages),
                "include_model_answer": include_model_answer,
                "specific_terminology": _split_lines(specific_terminology),
            }
            with st.spinner("Generating exam content…"):
                # Each passage takes ~30-90 s to generate; 240 s is the Azure
                # Container Apps ingress request limit.
                result, err = api_request(
                    "POST", "/content/generate", json=payload, timeout=GENERATE_TIMEOUT
                )
            if err:
                st.session_state.pop(_GENERATED_KEY, None)
                st.error(f"Generation failed: {err}")
            else:
                st.session_state[_GENERATED_KEY] = result or []

    passages = st.session_state.get(_GENERATED_KEY)
    if passages is MOCK_PASSAGES and not is_demo_mode():
        st.session_state.pop(_GENERATED_KEY, None)
        passages = None
    if passages is not None:
        st.subheader(f"Generated Passages ({len(passages)})")
        if not passages:
            st.info("The API returned no passages.")
        for idx, passage in enumerate(passages, 1):
            with st.container(border=True):
                st.markdown(f"**Passage {idx}**")
                render_passage(passage, key_prefix="gen")

with tab_queue:
    st.subheader("Passages Pending Expert Review")
    if st.button("Load pending queue", type="primary"):
        if is_demo_mode():
            queue, err = MOCK_PASSAGES, None
        else:
            queue, err = api_request("GET", "/content/queue/pending")
        if err:
            st.error(f"Could not load review queue: {err}")
        else:
            st.session_state["exam_builder_queue"] = queue
    queue = st.session_state.get("exam_builder_queue")
    if queue is MOCK_PASSAGES and not is_demo_mode():
        st.session_state.pop("exam_builder_queue", None)
        queue = None
    if queue is not None:
        domain_filter = st.multiselect("Filter by domain", options=DOMAINS, default=[])
        shown = [p for p in queue if not domain_filter or p.get("domain") in domain_filter]
        if not shown:
            st.info("No passages pending review.")
        for p in shown:
            with st.expander(f"`{p.get('id')}` — {p.get('domain')} / {p.get('difficulty')} · {p.get('word_count', 0)} words"):
                render_passage(p, key_prefix="queue")
