"""LinguaProctor — Main Streamlit Dashboard Application.

Multi-page app providing:
- Exam Builder: generate and review bilingual exam content
- Score Explorer: explore translation scoring results
- Calibration Dashboard: AI-vs-human agreement metrics
- Audio Review: oral exam transcript and scoring review
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).resolve().parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

import streamlit as st  # noqa: E402

from components.api import API_BASE_URL, DEMO_MODE_KEY, check_health  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("linguaproctor.dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")

# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="LinguaProctor",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/translation.png",
        width=64,
    )
    st.title("LinguaProctor")
    st.caption("AI‑Powered Bilingual Exam Platform")
    st.divider()

    # API status indicator
    st.subheader("🔌 API Status")
    is_healthy, detail = check_health()
    if is_healthy:
        st.success(f"✅ {detail}  •  `{API_BASE_URL}`")
    else:
        st.error(f"❌ {detail}  •  `{API_BASE_URL}`")
        st.info("API calls will fail and show errors. Enable *Demo data* below to browse sample results.")

    st.divider()

    demo = st.toggle(
        "Demo data",
        value=False,
        key=DEMO_MODE_KEY,
        help="Render static sample data instead of calling the API. Off by default.",
    )
    if demo:
        st.warning("DEMO DATA mode is ON: results are static samples, not from the API.")

    st.divider()

    # Role selector (for demo / RBAC simulation)
    role = st.selectbox(
        "Current Role",
        options=["examiner", "reviewer", "admin"],
        index=0,
        help="Select a role to control visible actions.",
    )

    st.divider()
    st.caption("© 2026 LinguaProctor · v0.1.0")

# Persist shared state
st.session_state["api_healthy"] = is_healthy
st.session_state["user_role"] = role

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

_PAGES_DIR = _DASHBOARD_DIR / "pages"
pg = st.navigation(
    [
        st.Page(str(_PAGES_DIR / "exam_builder.py"), title="Exam Builder", icon="📝"),
        st.Page(str(_PAGES_DIR / "score_explorer.py"), title="Score Explorer", icon="📊"),
        st.Page(str(_PAGES_DIR / "calibration.py"), title="Calibration", icon="📈"),
        st.Page(str(_PAGES_DIR / "audio_review.py"), title="Audio Review", icon="🎙️"),
    ]
)

pg.run()
