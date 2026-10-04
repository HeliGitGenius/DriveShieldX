"""Authority view of online (Razorpay) payments: every payment link, its status,
how it was confirmed, sync with Razorpay, and refunds. Rendered under Notice Desk."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def render_payments_admin() -> None:
    from backend import razorpay_gateway
    from database import payments_db

    st.markdown("<div class='section-title' style='margin-top:1.2rem'>Online payments · Razorpay</div>",
                unsafe_allow_html=True)
    cfg = razorpay_gateway.config()
    if cfg is None:
        st.info("Razorpay is not configured, so owners see the built-in demo checkout. Add "
                "RAZORPAY_KEY_ID_TEST / RAZORPAY_KEY_SECRET_TEST to .env and restart to enable it.")
        return
    badge = "LIVE (real money)" if cfg.mode == "live" else "TEST (no real money)"
    c = st.columns([2, 2, 1])
    c[0].markdown(f"Mode: **{badge}**")
    c[1].markdown(f"Webhook secret: **{'set' if cfg.webhook_secret else 'not set'}**")
    if cfg.mode == "live":
        st.warning(f"Live mode is capped at ₹{cfg.live_max_amount:.0f} per challan (DSX_LIVE_MAX_AMOUNT). "
                   "Use it only for your own ₹1 demo payment and refund it afterwards.")
    if c[2].button("Sync all", use_container_width=True, key="rzp_sync_all"):
        with st.spinner("Asking Razorpay for the status of open links…"):
            n = razorpay_gateway.sync_open_links()
        st.success(f"{n} newly paid link(s) settled.")
        st.rerun()

    rows = payments_db.list_links(300)
    if not rows:
        st.caption("No payment links yet. They are created when an owner chooses Razorpay at checkout.")
        return
    df = pd.DataFrame(rows)
    df["amount (₹)"] = df["amount_paise"] / 100
    st.dataframe(df[["link_id", "notice_id", "mode", "amount (₹)", "status", "method", "payment_id",
                     "settled_via", "refund_id", "refund_status", "created_at", "paid_at", "short_url"]],
                 use_container_width=True, hide_index=True)

    pick = st.selectbox("Payment link", [r["link_id"] for r in rows], key="rzp_pick",
                        format_func=lambda i: next(f"{r['link_id']} · challan #{r['notice_id']} · {r['status']}"
                                                   for r in rows if r["link_id"] == i))
    row = next(r for r in rows if r["link_id"] == pick)
    b = st.columns(2)
    if b[0].button("Check status with Razorpay", use_container_width=True, key="rzp_sync_one"):
        try:
            st.success(f"Razorpay says: {razorpay_gateway.sync_link(pick)}")
        except Exception as exc:
            st.error(str(exc))
    if row["status"] == "paid":
        confirm = st.checkbox(f"Yes, refund ₹{row['amount_paise'] / 100:,.2f} for challan #{row['notice_id']}",
                              key=f"rzp_refund_ok_{pick}")
        if b[1].button("Refund", use_container_width=True, disabled=not confirm, key="rzp_refund"):
            try:
                rf = razorpay_gateway.refund(pick)
                st.success(f"Refund {rf.get('id')} · {rf.get('status')}. Challan #{row['notice_id']} is pending again.")
            except Exception as exc:
                st.error(str(exc))
