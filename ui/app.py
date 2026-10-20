import httpx
import streamlit as st

from backend.config import get_settings

st.set_page_config(page_title="DeepDive", page_icon="🔎")
st.title("DeepDive")
st.caption("Agent Arena 2026 — foundation only; no agents wired yet.")

if st.button("Check backend health"):
    try:
        resp = httpx.get(f"{get_settings().api_url}/health", timeout=5)
        st.json(resp.json())
    except httpx.HTTPError as exc:
        st.error(f"Backend unreachable: {exc}")
