"""HITL review panel: approve / override / escalate the ReviewItem for an assessment.

Uses ``GET /review/queue`` to locate the item whose ``assessment_id`` matches,
then ``POST /review/{id}/approve|override|escalate``.
"""

from __future__ import annotations

import streamlit as st

from components.api import api_request, is_demo_mode
from components.mock_data import mock_review_item


def find_review_item(assessment_id: str) -> tuple[dict | None, str | None]:
    """Return the ReviewItem for ``assessment_id`` (or an error message)."""
    items, err = api_request("GET", "/review/queue")
    if err:
        return None, err
    match = next((i for i in items or [] if i.get("assessment_id") == assessment_id), None)
    if match is None:
        return None, f"No review item found for assessment {assessment_id}."
    return match, None


def _show_item_summary(item: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("Status", item.get("status", "?"))
    cols[1].metric("Priority", item.get("priority", "?"))
    cols[2].metric("Approvals", f"{len(item.get('approvals') or [])}/{item.get('required_approvals', 1)}")
    cols[3].metric("AI score", f"{item.get('ai_score', 0):g}")
    final = item.get("final_score")
    cols[4].metric("Final score", "—" if final is None else f"{final:g}")
    if item.get("flags"):
        st.caption("Flags: " + ", ".join(item["flags"]))


def render_review_panel(assessment_id: str, assessment_type: str, criteria: list[dict], key_prefix: str) -> None:
    """Render the reviewer workflow for one assessment."""
    st.subheader("Human Review")
    demo = is_demo_mode()

    if demo:
        item, err = mock_review_item(assessment_id, assessment_type, criteria), None
    else:
        item, err = find_review_item(assessment_id)
    if err:
        st.error(f"Could not load review item: {err}")
        return
    assert item is not None

    _show_item_summary(item)

    role = st.session_state.get("user_role", "examiner")
    if role not in ("reviewer", "admin"):
        st.info("Review actions require the *reviewer* or *admin* role (sidebar).")
        return

    closed = item.get("status") in ("approved", "overridden")
    if closed:
        st.info(f"This review item is closed ({item['status']}); no further actions allowed.")
        return

    reviewer_id = st.text_input("Reviewer ID", key=f"{key_prefix}_reviewer_id")
    item_id = item["id"]

    def _submit(action: str, body: dict) -> None:
        if not reviewer_id.strip():
            st.error("Reviewer ID is required.")
            return
        if demo:
            st.info(f"Demo mode: `{action}` was NOT sent to the API.")
            return
        result, err = api_request("POST", f"/review/{item_id}/{action}", json={"reviewer_id": reviewer_id.strip(), **body})
        if err:
            st.error(f"{action.title()} failed: {err}")
        else:
            st.success(f"{action.title()} recorded. Status: {result.get('status')}, final score: {result.get('final_score')}")

    tab_approve, tab_override, tab_escalate = st.tabs(["Approve", "Override", "Escalate"])

    with tab_approve:
        if st.button("Approve AI scores", key=f"{key_prefix}_approve", type="primary"):
            _submit("approve", {})

    with tab_override:
        ai_scores: dict[str, float] = item.get("ai_criterion_scores") or {c["criterion"]: c["score"] for c in criteria}
        crit_max = {c["criterion"]: float(c["max_score"]) for c in criteria}
        default_max = float(item.get("criterion_max", 5.0))
        with st.form(f"{key_prefix}_override_form"):
            cols = st.columns(max(len(ai_scores), 1))
            new_scores: dict[str, float] = {}
            for idx, (name, score) in enumerate(ai_scores.items()):
                with cols[idx]:
                    new_scores[name] = st.number_input(
                        name,
                        min_value=0.0,
                        max_value=crit_max.get(name, default_max),
                        value=min(float(score), crit_max.get(name, default_max)),
                        step=0.5,
                        key=f"{key_prefix}_ov_{name}",
                    )
            justification = st.text_area("Justification (min 10 characters)", key=f"{key_prefix}_justification")
            if st.form_submit_button("Submit override", type="primary"):
                changed = {k: v for k, v in new_scores.items() if v != float(ai_scores[k])}
                if not changed:
                    st.error("No criterion score was changed.")
                elif len(justification.strip()) < 10:
                    st.error("Justification must be at least 10 characters.")
                else:
                    _submit("override", {"overridden_scores": changed, "justification": justification.strip()})

    with tab_escalate:
        reason = st.text_input("Reason for escalation", key=f"{key_prefix}_escalate_reason")
        if st.button("Escalate to panel", key=f"{key_prefix}_escalate"):
            if len(reason.strip()) < 3:
                st.error("Reason must be at least 3 characters.")
            else:
                _submit("escalate", {"reason": reason.strip()})
