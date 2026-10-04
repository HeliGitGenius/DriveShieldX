"""Authority panel: SMS gateway status, a test message, and the e-challan SMS log."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st


def render_sms_admin() -> None:
    from backend.alerts import deliver_sms, normalise_in_phone, sms_gate
    from backend import challan_sms

    st.markdown("<div class='section-title' style='margin-top:1.2rem'>SMS · e-challan notifications</div>",
                unsafe_allow_html=True)
    provider = (os.getenv("DSX_SMS_PROVIDER") or "").strip() or "none (outbox files only)"
    allow = [x for x in os.getenv("DSX_SMS_ALLOWLIST", "").split(",") if normalise_in_phone(x)]
    c = st.columns(3)
    c[0].markdown(f"Gateway: **{provider}**")
    c[1].markdown(f"Allow-listed numbers: **{'ALL (DSX_SMS_ALLOW_ALL=1)' if os.getenv('DSX_SMS_ALLOW_ALL') == '1' else len(allow)}**")
    if c[2].button("Send pending challan SMS now", use_container_width=True, key="sms_now"):
        st.success(f"Result: {challan_sms.notify_pending(min_interval_s=0) or 'nothing new'}")

    with st.expander("Send a test SMS"):
        num = st.text_input("Mobile number (must be in DSX_SMS_ALLOWLIST)", key="sms_test_num")
        if st.button("Send test", key="sms_test_btn"):
            why = sms_gate(num)
            if why:
                st.error(f"Not sent for real: {why}.")
            else:
                _, outcome = deliver_sms(num, "DriveShieldX test: SMS gateway is working.")
                (st.success if outcome == "delivered" else st.warning)(f"Outcome: {outcome}")

    rows = challan_sms.recent(100)
    if rows:
        df = pd.DataFrame(rows)[["kind", "ref_id", "status", "phone_masked", "detail", "created_at"]]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No challan SMS yet. Each newly issued challan for a registered owner gets one SMS.")
