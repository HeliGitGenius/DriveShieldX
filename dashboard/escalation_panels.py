"""Unpaid-challan escalation: owner view (contest, hotlist warning) and officer view
(disputes, hotlist, live sightings, court referrals)."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _demo_banner() -> None:
    from backend import escalation

    if escalation.demo_mode():
        st.warning(f"DEMO TIME: one escalation 'day' = {escalation.settings()['day_seconds']:.0f} s "
                   "(DSX_ESCALATION_DAY_SECONDS). Real deployments use 86400.")


def render_owner_escalation(owner: dict) -> None:
    from backend import escalation
    from database.db_manager import get_owner_notices

    notices = get_owner_notices(int(owner["owner_id"]))
    plates = {n.get("plate_number") for n in notices if n.get("plate_number")}
    for p in sorted(plates):
        h = escalation.is_hotlisted(p)
        if h:
            st.markdown(
                f"<div class='susp-banner'>⛔ Vehicle <b>{p}</b> is marked <b>NOT TO BE TRANSACTED</b>: RC and "
                f"driving-licence services (renewal, transfer, NOC) are blocked until ₹{h['amount_due']:,.0f} of "
                f"overdue challans is paid. Paying clears it automatically.</div>", unsafe_allow_html=True)
    open_ones = [n for n in notices if n.get("payment_status") in ("pending", "overdue")]
    mine = [d for d in escalation.disputes() if d.get("owner_id") == owner["owner_id"]]
    if not open_ones and not mine:
        return
    with st.expander("Contest a challan (within 45 days of issue)", expanded=False):
        _demo_banner()
        st.caption("If you believe a challan is wrong (not your vehicle, wrong plate read, emergency, ...), contest it "
                   "here. While it is under review it does not count against you and no reminders are sent.")
        for n in open_ones:
            ok, why = escalation.can_contest(n)
            cols = st.columns([3, 2])
            cols[0].markdown(f"**Challan #{n['notice_id']}** · {n.get('plate_number')} · "
                             f"₹{float(n.get('amount') or 0) + float(n.get('late_fee') or 0):,.0f} · {why}")
            if ok:
                reason = cols[1].text_input("Reason", key=f"disp_reason_{n['notice_id']}", label_visibility="collapsed",
                                            placeholder="Why is this challan wrong?")
                if cols[1].button("Contest", key=f"disp_btn_{n['notice_id']}", disabled=len(reason.strip()) < 10):
                    try:
                        escalation.file_dispute(int(n["notice_id"]), int(owner["owner_id"]), reason)
                        st.success("Submitted. You will get an SMS with the officer's decision.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
        if mine:
            st.write("**Your contests**")
            st.dataframe(pd.DataFrame(mine)[["notice_id", "plate_number", "status", "filed_at", "decision_note"]],
                         use_container_width=True, hide_index=True)


def render_escalation_admin() -> None:
    from backend import escalation

    st.markdown("<div class='section-title' style='margin-top:1.2rem'>Unpaid challans · escalation</div>",
                unsafe_allow_html=True)
    s = escalation.settings()
    _demo_banner()
    st.caption(f"Reminders on day {', '.join(f'{d:g}' for d in s['reminders'])}; contest window {s['contest_days']:.0f} "
               f"days; unpaid after day {s['hotlist_after']:.0f} → vehicle NOT TO BE TRANSACTED (hotlist, live camera "
               f"alerts); day {s['court_after']:.0f} → virtual-court referral. 5+ challans in a year suspend the licence. "
               f"No money is ever deducted without the owner's consent.")
    if st.button("Run escalation now", key="esc_run"):
        st.success(f"Done: {escalation.run_once(force=True)}")

    sight = escalation.sightings(unacknowledged_only=True)
    st.write(f"**Live sightings of hotlisted vehicles** ({len(sight)} unacknowledged)")
    if sight:
        st.dataframe(pd.DataFrame(sight)[["sighting_id", "plate_key", "camera_id", "seen_at"]],
                     use_container_width=True, hide_index=True)
        sid = st.selectbox("Sighting", [x["sighting_id"] for x in sight], key="esc_sight")
        if st.button("Acknowledge (patrol informed)", key="esc_ack"):
            escalation.acknowledge_sighting(int(sid), (st.session_state.get("authority") or {}).get("name", "officer"))
            st.rerun()

    pend = escalation.disputes("submitted")
    st.write(f"**Contested challans awaiting decision** ({len(pend)})")
    if pend:
        st.dataframe(pd.DataFrame(pend)[["dispute_id", "notice_id", "plate_number", "violation_kind", "amount",
                                         "reason", "filed_at"]], use_container_width=True, hide_index=True)
        did = st.selectbox("Dispute", [d["dispute_id"] for d in pend], key="esc_disp")
        note = st.text_input("Decision note (sent to the owner)", key="esc_note")
        b1, b2 = st.columns(2)
        officer = (st.session_state.get("authority") or {}).get("name", "officer")
        for col, accept, label in ((b1, True, "Accept — cancel challan"), (b2, False, "Reject — challan stands")):
            if col.button(label, key=f"esc_dec_{accept}", disabled=not note.strip()):
                try:
                    escalation.decide_dispute(int(did), accept, officer, note)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    hot = escalation.hotlist()
    st.write(f"**Vehicles marked NOT TO BE TRANSACTED** ({len(hot)})")
    if hot:
        st.dataframe(pd.DataFrame(hot)[["plate_key", "amount_due", "notice_ids", "listed_at", "reason"]],
                     use_container_width=True, hide_index=True)

    court = escalation.court_referrals()
    st.write(f"**Referred to virtual court** ({len(court)})")
    if court:
        df = pd.DataFrame(court)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("Download court referral list (CSV)", df.to_csv(index=False).encode("utf-8"),
                           file_name="court_referrals.csv", mime="text/csv", key="esc_csv")
