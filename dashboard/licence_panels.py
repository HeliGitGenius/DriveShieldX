"""Repeat-offender standing for the owner and licence actions for the officer."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st


def _d(v) -> str:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(v or "-")


def render_owner_standing(owner: dict) -> None:
    from backend import enforcement

    s = enforcement.standing(int(owner["owner_id"]))
    n = s["offences"]
    labels = ["1st offence · ₹500 + warning", "2nd offence · ₹1000", "3rd offence · ₹3000 + licence suspension"]
    dots = ""
    for i in range(3):
        on = i < min(n, 3)
        col = "#FF6B6B" if i == 2 and on else ("var(--gold-hi)" if on else "rgba(242,169,59,.25)")
        dots += (f"<div style='flex:1;text-align:center'><div style='height:8px;border-radius:4px;background:{col};"
                 f"margin-bottom:.4rem'></div><div style='font-size:.78rem;color:{'#F4ECD8' if on else 'var(--muted)'}'>"
                 f"{labels[i]}</div></div>")
    if s["next_suspends"]:
        nxt = (f"⚠ Your next offence: ₹{s['next_amount']:,.0f} fine and a {s['next_months']}-month "
               f"driving-licence suspension.")
    else:
        nxt = f"Your next offence: ₹{s['next_amount']:,.0f} fine."
    st.markdown(
        f"<div class='panel-card'><div class='section-title'>Your standing · {n} offence(s) in the last "
        f"{s['window_days']} days</div><div style='display:flex;gap:.8rem;margin:.6rem 0'>{dots}</div>"
        f"<div class='small-muted'>{nxt}</div></div>", unsafe_allow_html=True)
    sus = s["suspension"]
    if sus:
        st.markdown(
            f"<div class='susp-banner'>⛔ <b>Driving licence SUSPENDED</b> from {_d(sus['starts_on'])} to "
            f"<b>{_d(sus['ends_on'])}</b> ({sus['months']} month(s)) — {sus.get('reason') or ''}. "
            f"Driving during this period is a further offence. Contact the transport authority for any appeal.</div>",
            unsafe_allow_html=True)
    else:
        st.markdown("<div class='small-muted' style='margin:.3rem 0 1rem'>Driving licence status: "
                    "<b style='color:#7fd1b9'>VALID</b></div>", unsafe_allow_html=True)


def render_licence_admin() -> None:
    from backend import enforcement

    st.markdown("<div class='section-title' style='margin-top:1.2rem'>Licence actions · repeat offenders</div>",
                unsafe_allow_html=True)
    st.caption(f"Fines follow the schedule automatically (1st ₹500, 2nd ₹1000, 3rd+ ₹3000). The 3rd offence within "
               f"{enforcement.window_days()} days suspends the owner's licence for 3 months, +1 month per further "
               f"offence. Suspensions end automatically on their end date.")
    with st.expander("Look up a driver / vehicle record (full history, never deleted)", expanded=False):
        render_record_lookup("desk")
    rows = enforcement.all_actions()
    if not rows:
        st.caption("No licence suspensions yet.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df[["action_id", "owner_name", "notice_id", "months", "starts_on", "ends_on", "status",
                     "reason", "lifted_by", "lift_reason"]], use_container_width=True, hide_index=True)
    active = [r for r in rows if r["status"] == "active"]
    if active:
        pick = st.selectbox("Active suspension", [r["action_id"] for r in active], key="lic_pick",
                            format_func=lambda i: next(f"#{i} · {r['owner_name']} · until {r['ends_on']}"
                                                       for r in active if r["action_id"] == i))
        reason = st.text_input("Reason for lifting (appeal decision, error, ...)", key="lic_reason")
        if st.button("Lift suspension", key="lic_lift", disabled=not reason.strip()):
            officer = (st.session_state.get("authority") or {}).get("name", "officer")
            if enforcement.lift(int(pick), officer, reason):
                st.success("Suspension lifted and recorded.")
                st.rerun()


def render_driver_record(rec: dict, key: str = "") -> None:
    """Permanent record of a vehicle / licence holder for an officer."""
    lic = rec["licence"]
    colour = "#ff8080" if lic.startswith("SUSPENDED") else ("#7fd1b9" if lic == "VALID" else "var(--muted)")
    owner = (rec.get("owner") or {}).get("name") or "—"
    st.markdown(
        f"<div class='panel-card'><b>{rec['plate'] or '—'}</b> · owner {owner} · licence "
        f"<b style='color:{colour}'>{lic}</b><br><span class='small-muted'>Strikes in current cycle: "
        f"{rec['current_strikes'] if rec['current_strikes'] is not None else '—'}"
        f"{' (counting since ' + str(rec['counting_since'])[:10] + ')' if rec.get('counting_since') else ''}"
        f" · lifetime e-challans: {rec['lifetime_challans']} · helmet/triple/seat-belt records: "
        f"{len(rec['rule_violations'])} · suspensions on record: {len(rec['suspensions'])}</span></div>",
        unsafe_allow_html=True)
    if rec["challans"]:
        st.dataframe(pd.DataFrame(rec["challans"]), use_container_width=True, hide_index=True)
    if rec["suspensions"]:
        st.dataframe(pd.DataFrame(rec["suspensions"])[["action_id", "months", "starts_on", "ends_on", "status",
                                                      "reason", "lift_reason"]],
                     use_container_width=True, hide_index=True)
    if rec["rule_violations"]:
        st.dataframe(pd.DataFrame(rec["rule_violations"]), use_container_width=True, hide_index=True)


def render_record_lookup(key: str = "lookup") -> None:
    from backend import enforcement

    plate = st.text_input("Driver / vehicle record — enter plate number", key=f"{key}_plate",
                          placeholder="e.g. MH01AB1234")
    if plate.strip():
        render_driver_record(enforcement.driver_record(plate=plate), key=key)


def render_auto_issue_admin() -> None:
    """What happened to each new helmet / triple-riding / seat-belt detection."""
    import pandas as pd
    import streamlit as st
    from backend import auto_issue

    st.markdown("<div class='section-title' style='margin-top:1.2rem'>Automatic e-challans · plate check</div>",
                unsafe_allow_html=True)
    if not auto_issue.enabled():
        st.info("Automatic issuing is off (DSX_AUTO_ISSUE_RULES=0). Officers issue from Rule Violations.")
        return
    rows = auto_issue.recent()
    if not rows:
        st.caption("No new detections checked yet. Each new detection is checked automatically: "
                   "plate read → registry → e-challan, or officer review with the reason.")
        return
    df = pd.DataFrame(rows)
    counts = df["outcome"].value_counts().to_dict()
    cols = st.columns(4)
    for col, key, label in zip(cols, ["issued", "not_registered", "invalid_plate", "no_plate"],
                               ["Issued automatically", "Not in registry", "Plate misread", "No plate read"]):
        col.metric(label, counts.get(key, 0))
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Anything not issued stays on the Rule Violations page for an officer, who can type the "
               "plate from the snapshot and issue the e-challan there.")
