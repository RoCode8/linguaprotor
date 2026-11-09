"""Audio Review — Submit and review oral interpretation assessments.

``POST /api/v1/oral/assess`` (multipart: exam_id, candidate_id, mode,
source_text, source_language, target_language, audio_file) and
``GET /api/v1/oral/{result_id}`` both return an OralAssessmentResult.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DASHBOARD_DIR = str(Path(__file__).resolve().parents[1])
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import streamlit as st  # noqa: E402

from components.api import api_request, demo_banner, is_demo_mode  # noqa: E402
from components.mock_data import MOCK_ORAL_RESULT  # noqa: E402
from components.review_panel import render_review_panel  # noqa: E402
from components.score_heatmap import render_criteria_chart, render_score_gauge  # noqa: E402

_RESULT_KEY = "audio_review_result"
_AUDIO_KEY = "audio_review_candidate_audio"
MODES = ["consecutive", "simultaneous", "dialogue"]

st.header("Audio Review")
st.caption("Submit oral interpretation recordings and review transcripts, alignment and scores.")
if is_demo_mode():
    demo_banner()


def _store(result: dict | None, err: str | None, audio: bytes | None = None) -> None:
    if err:
        st.session_state.pop(_RESULT_KEY, None)
        st.session_state.pop(_AUDIO_KEY, None)
        st.error(f"Oral assessment request failed: {err}")
    else:
        st.session_state[_RESULT_KEY] = result
        st.session_state[_AUDIO_KEY] = audio


tab_submit, tab_lookup = st.tabs(["Assess Recording", "Look Up Result"])

with tab_submit:
    with st.form("oral_form"):
        c1, c2, c3 = st.columns(3)
        exam_id = c1.text_input("Exam ID", value="oral-exam")
        candidate_id = c2.text_input("Candidate ID", value="oral-candidate")
        mode = c3.selectbox("Mode", MODES)
        c4, c5 = st.columns(2)
        source_language = c4.text_input("Source language", value="en")
        target_language = c5.text_input("Target language", value="ar")
        source_text = st.text_area("Source text (what the candidate interpreted)", height=100)
        audio_file = st.file_uploader("Candidate recording (WAV)", type=["wav"])
        submit_btn = st.form_submit_button("Assess", type="primary", width="stretch")

    if submit_btn:
        if is_demo_mode():
            _store(MOCK_ORAL_RESULT, None)
        elif not source_text.strip():
            st.error("Source text is required.")
        elif audio_file is None:
            st.error("Upload a WAV recording.")
        else:
            audio_bytes = audio_file.getvalue()
            form = {
                "exam_id": exam_id.strip() or "oral-exam",
                "candidate_id": candidate_id.strip() or "oral-candidate",
                "mode": mode,
                "source_text": source_text,
                "source_language": source_language.strip() or "en",
                "target_language": target_language.strip() or "ar",
            }
            files = {"audio_file": (audio_file.name, audio_bytes, audio_file.type or "audio/wav")}
            with st.spinner("Transcribing and scoring…"):
                result, err = api_request("POST", "/oral/assess", data=form, files=files)
            _store(result, err, audio_bytes)

with tab_lookup:
    result_id = st.text_input("Oral result ID", placeholder="UUID returned by /oral/assess")
    if st.button("Load Assessment", type="primary", width="stretch"):
        if is_demo_mode():
            _store(MOCK_ORAL_RESULT, None)
        elif not result_id.strip():
            st.error("Enter a result ID.")
        else:
            with st.spinner("Loading assessment…"):
                result, err = api_request("GET", f"/oral/{result_id.strip()}")
            _store(result, err)

data: dict | None = st.session_state.get(_RESULT_KEY)
if data is not None and data is MOCK_ORAL_RESULT and not is_demo_mode():
    st.session_state.pop(_RESULT_KEY, None)
    data = None


def _words_table(transcript: dict) -> None:
    words = transcript.get("words") or []
    if not words:
        st.caption("No word-level timestamps.")
        return
    st.dataframe(
        [
            {"word": w["word"], "start_s": w["start_ms"] / 1000, "end_s": w["end_ms"] / 1000, "confidence": w.get("confidence")}
            for w in words
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "start_s": st.column_config.NumberColumn("Start (s)", format="%.2f"),
            "end_s": st.column_config.NumberColumn("End (s)", format="%.2f"),
            "confidence": st.column_config.NumberColumn("Conf.", format="%.2f"),
        },
    )


if data:
    st.divider()
    st.caption(
        f"Result `{data.get('id')}` · exam `{data.get('exam_id')}` · candidate `{data.get('candidate_id')}` · mode `{data.get('mode')}`"
    )

    m = st.columns(3)
    with m[0]:
        render_score_gauge(data["overall_score"], data["overall_max"], "Overall Score")
    m[1].metric("Confidence", f"{data['confidence']:.0%}")
    m[2].metric("Flags", str(len(data.get("flags") or [])))
    for flag in data.get("flags") or []:
        st.warning(f"Flag: {flag}")

    audio_bytes = st.session_state.get(_AUDIO_KEY)
    if audio_bytes:
        st.subheader("Candidate Audio")
        st.audio(audio_bytes, format="audio/wav")

    st.subheader("Transcripts")
    t1, t2 = st.columns(2)
    for col, label, key in ((t1, "Source", "source_transcript"), (t2, "Candidate", "candidate_transcript")):
        tr = data.get(key) or {}
        with col:
            st.markdown(f"**{label} transcript** ({tr.get('language', '?')}, {tr.get('duration_seconds', 0):.2f}s)")
            st.markdown(f"> {tr.get('text') or '_(empty)_'}")
            with st.expander("Word timestamps"):
                _words_table(tr)

    st.subheader("Segment Alignment")
    alignments = data.get("alignments") or []
    if alignments:
        st.dataframe(
            [
                {
                    "source_segment": a["source_segment"],
                    "source_ms": f"{a['source_start_ms']}–{a['source_end_ms']}",
                    "candidate_segment": a["candidate_segment"],
                    "candidate_ms": (
                        f"{a['candidate_start_ms']}–{a['candidate_end_ms']}"
                        if a.get("candidate_start_ms") is not None
                        else "—"
                    ),
                    "alignment_score": a["alignment_score"],
                }
                for a in alignments
            ],
            width="stretch",
            hide_index=True,
            column_config={"alignment_score": st.column_config.ProgressColumn("Score", min_value=0.0, max_value=1.0)},
        )
    else:
        st.caption("No alignments returned.")

    st.subheader("Oral Criterion Scores")
    render_criteria_chart(data.get("criteria") or [])
    for crit in data.get("criteria") or []:
        with st.expander(f"**{crit['criterion']}** — {crit['score']:g}/{crit['max_score']:g}"):
            st.markdown(crit.get("summary", ""))
            for ev in crit.get("evidence") or []:
                st.markdown(f"- {ev}")

    st.subheader("Audio Quality")
    aq = data.get("audio_quality") or {}
    q = st.columns(4)
    q[0].metric("Duration", f"{aq.get('duration_seconds', 0):.2f}s")
    q[1].metric("Sample rate", "n/a" if aq.get("sample_rate") is None else f"{aq['sample_rate']} Hz")
    q[2].metric("Est. SNR", "n/a" if aq.get("estimated_snr_db") is None else f"{aq['estimated_snr_db']:.1f} dB")
    q[3].metric("Acceptable", "Yes" if aq.get("is_acceptable") else "No")
    for issue in aq.get("issues") or []:
        st.warning(f"Audio issue: {issue}")

    st.divider()
    render_review_panel(data["id"], "oral", data.get("criteria") or [], key_prefix="oral")
