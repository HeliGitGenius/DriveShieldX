"""Road Safety + Analytics pages for the authority dashboard.

Imported by dashboard/app.py; the `hero` banner helper is passed in to avoid a
circular import. Everything here reads/writes only the new safety tables plus
read-only queries on the existing ones.
"""
from __future__ import annotations

import json
from typing import Callable

import pandas as pd
import plotly.express as px
import streamlit as st

from database import db_manager, safety_db


# ------------------------------------------------------------------ helpers
def _q(sql: str, params=()) -> pd.DataFrame:
    c = db_manager.get_connection()
    try:
        return pd.read_sql_query(sql, c, params=params)
    finally:
        c.close()


def _cameras() -> pd.DataFrame:
    return _q("SELECT camera_id, location FROM CAMERA ORDER BY camera_id")


# ================================================================== ROAD SAFETY
def page_road_safety(hero: Callable) -> None:
    safety_db.conn().close()  # make sure the safety tables exist
    hero("Road Safety",
         "Fog / visibility advisories, confirmed accidents with police + hospital dispatch, "
         "emergency facilities and vehicle registry look-ups.",
         eyebrow="DriveShieldX · Safety Layer")
    t1, t2, t3, t4, t5 = st.tabs(["Status", "Accidents", "Camera location & facilities", "Fog calibration",
                                  "Vehicle registry"])

    with t1:
        fog = pd.DataFrame(safety_db.list_fog_events(limit=500))
        acc = pd.DataFrame(safety_db.list_accidents(limit=500))
        c1, c2, c3 = st.columns(3)
        active = 0
        if not fog.empty:
            last = fog.sort_values("created_at").groupby("camera_id").tail(1)
            active = int((last["event_type"] == "advisory_on").sum())
        c1.metric("Cameras under fog advisory", active)
        c2.metric("Accidents (all time)", 0 if acc.empty else len(acc))
        c3.metric("Awaiting dispatch / review", 0 if acc.empty else int(acc["status"].isin(["candidate", "confirmed"]).sum()))
        st.subheader("Fog advisory log")
        if fog.empty:
            st.info("No fog advisories yet. The visibility monitor runs automatically on every live/uploaded video.")
        else:
            st.dataframe(fog[["created_at", "camera_id", "event_type", "level", "visibility_index", "advisory_speed",
                              "fog_score"]], use_container_width=True, hide_index=True)
            st.plotly_chart(px.line(fog.sort_values("created_at"), x="created_at", y="visibility_index",
                                    color="camera_id", markers=True, title="Visibility index at advisory changes"),
                            use_container_width=True)

    with t2:
        acc = pd.DataFrame(safety_db.list_accidents(limit=200))
        if acc.empty:
            st.info("No accidents detected. Confirmed incidents appear here with their evidence frame and dispatch log.")
        else:
            st.dataframe(acc[["accident_id", "created_at", "camera_id", "track_ids", "score", "status"]],
                         use_container_width=True, hide_index=True)
            pick = st.selectbox("Incident", acc["accident_id"].tolist(), key="acc_pick")
            row = acc[acc["accident_id"] == pick].iloc[0]
            left, right = st.columns([1, 1])
            with left:
                if row.get("snapshot_path"):
                    try:
                        st.image(row["snapshot_path"], caption=f"Accident #{pick} evidence", use_container_width=True)
                    except Exception:
                        st.caption("Snapshot file not found on this machine.")
                st.json(json.loads(row["cues"] or "{}"))
            with right:
                disp = pd.DataFrame(safety_db.list_dispatches(int(pick)))
                st.write("**Dispatch log**")
                st.dataframe(disp if not disp.empty else pd.DataFrame([{"info": "no dispatch yet"}]),
                             use_container_width=True, hide_index=True)
                b1, b2, b3 = st.columns(3)
                who = (st.session_state.get("authority") or {}).get("name", "officer")
                if b1.button("Re-dispatch", key=f"redis_{pick}"):
                    from backend.dispatch import dispatch_accident
                    dispatch_accident(int(pick), int(row["camera_id"] or 1), str(row["created_at"]), float(row["score"]),
                                      json.loads(row["cues"] or "{}"), str(row["track_ids"]).split(","))
                    st.rerun()
                if b2.button("False alarm", key=f"fa_{pick}"):
                    safety_db.update_accident_status(int(pick), "false_alarm", who); st.rerun()
                if b3.button("Resolved", key=f"res_{pick}"):
                    safety_db.update_accident_status(int(pick), "resolved", who); st.rerun()
            st.write("**Vehicles involved — driver records**")
            try:
                from backend import enforcement
                from dashboard.licence_panels import render_driver_record, render_record_lookup
                tids = [t.strip() for t in str(row.get("track_ids") or "").split(",") if t.strip()]
                plates = []
                if tids and row.get("session_id") is not None:
                    q = ",".join("?" * len(tids))
                    plates = [p for p in _q(f"SELECT DISTINCT plate_text FROM VEHICLE WHERE session_id=? AND "
                                            f"tracker_id IN ({q}) AND plate_text IS NOT NULL AND plate_text!=''",
                                            [int(row["session_id"])] + tids)["plate_text"].tolist()]
                for p in plates:
                    render_driver_record(enforcement.driver_record(plate=p), key=f"acc{pick}_{p}")
                if not plates:
                    st.caption("No plate was read for the vehicles in this incident. Enter one to pull the record:")
                render_record_lookup(f"acc{pick}")
            except Exception as exc:
                st.caption(f"(driver records unavailable: {exc})")

    with t3:
        cams = _cameras()
        if cams.empty:
            st.warning("No cameras configured.")
        else:
            cam = st.selectbox("Camera", cams["camera_id"].tolist(),
                               format_func=lambda i: f"#{i} · {cams.set_index('camera_id').loc[i, 'location']}",
                               key="geo_cam")
            geo = safety_db.get_camera_geo(int(cam)) or {}
            with st.form("geo_form"):
                c1, c2 = st.columns(2)
                lat = c1.number_input("Latitude", value=float(geo.get("latitude") or 19.0760), format="%.6f")
                lon = c2.number_input("Longitude", value=float(geo.get("longitude") or 72.8777), format="%.6f")
                addr = st.text_input("Address / junction name", value=geo.get("address") or "")
                c3, c4 = st.columns(2)
                ph = c3.text_input("Control-room phone (receives accident SMS)", value=geo.get("control_room_phone") or "")
                em = c4.text_input("Control-room e-mail", value=geo.get("control_room_email") or "")
                if st.form_submit_button("Save camera location"):
                    safety_db.set_camera_geo(int(cam), lat, lon, addr, ph, em)
                    st.success("Saved.")
            st.caption("Latitude/longitude default to central Mumbai until you save the camera's real position.")
            c1, c2 = st.columns([1, 2])
            radius = c1.number_input("Search radius (m)", 500, 20000, 5000, step=500)
            if c2.button("Fetch hospitals & police stations from OpenStreetMap"):
                if not geo:
                    st.error("Save the camera location first.")
                else:
                    try:
                        from backend.dispatch import fetch_osm_facilities
                        n = fetch_osm_facilities(geo["latitude"], geo["longitude"], int(radius))
                        st.success(f"Imported {n['hospital']} hospitals and {n['police']} police stations from OpenStreetMap.")
                    except Exception as exc:
                        st.error(f"OpenStreetMap request failed: {exc}")
            with st.expander("Add a facility manually"):
                with st.form("fac_form"):
                    kind = st.selectbox("Type", ["hospital", "police"])
                    name = st.text_input("Name")
                    c1, c2 = st.columns(2)
                    flat = c1.number_input("Lat", value=float(geo.get("latitude") or 19.0760), format="%.6f", key="flat")
                    flon = c2.number_input("Lon", value=float(geo.get("longitude") or 72.8777), format="%.6f", key="flon")
                    fph = st.text_input("Phone")
                    fem = st.text_input("E-mail")
                    if st.form_submit_button("Add facility") and name:
                        safety_db.upsert_facility(kind, name, flat, flon, fph, fem)
                        st.success("Added.")
            facs = pd.DataFrame(safety_db.list_facilities())
            if not facs.empty:
                if geo:
                    from backend.dispatch import haversine_km
                    facs["distance_km"] = facs.apply(lambda r: round(haversine_km(geo["latitude"], geo["longitude"],
                                                                                   r["latitude"], r["longitude"]), 2), axis=1)
                    facs = facs.sort_values("distance_km")
                st.dataframe(facs[[c for c in ["facility_id", "kind", "name", "distance_km", "phone", "email", "source"]
                                   if c in facs.columns]], use_container_width=True, hide_index=True)
                mp = facs.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]]
                if geo:
                    mp = pd.concat([mp, pd.DataFrame([{"lat": geo["latitude"], "lon": geo["longitude"]}])])
                st.map(mp)

    with t4:
        cams = _cameras()
        if not cams.empty:
            cam = st.selectbox("Camera", cams["camera_id"].tolist(), key="fog_cam")
            base = safety_db.get_fog_baseline(int(cam))
            st.write("Current clear-weather baseline:", base or "not learned yet (learned automatically after ~20 s of clear video)")
            up = st.file_uploader("Upload a CLEAR-weather frame from this camera to calibrate", type=["jpg", "jpeg", "png"])
            if up is not None:
                import cv2
                import numpy as np
                from detection.fog_monitor import measure
                img = cv2.imdecode(np.frombuffer(up.read(), np.uint8), cv2.IMREAD_COLOR)
                m = measure(img)
                st.image(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), width=360)
                st.write({k: m[k] for k in ("dark_channel", "contrast", "edge_density", "brightness")})
                if st.button("Use as baseline"):
                    safety_db.set_fog_baseline(int(cam), {k: m[k] for k in ("dark_channel", "contrast", "edge_density")})
                    st.success("Baseline saved.")

    with t5:
        from backend.registry import REGISTRY
        plate = st.text_input("Plate number (e.g. MH12AB1234)", key="reg_plate")
        if plate:
            from detection.plate_format import correct
            fixed, subs = correct(plate)
            if fixed and subs:
                st.caption(f"Format-aware reading: **{fixed}** ({subs} character(s) corrected)")
            res = REGISTRY.lookup(fixed or plate)
            st.write(f"Valid Indian format: **{res['valid_format']}** · Found: **{res['found']}** · "
                     f"Source: {res['provider'] or '—'}")
            if res["error"]:
                st.warning(f"Remote registry error: {res['error']}")
            if res["record"]:
                st.json(res["record"])
            if res["flags"]:
                st.error("Document flags: " + ", ".join(res["flags"]))
            if not REGISTRY.http.enabled:
                st.caption("Only the local registry is active. Set DSX_RC_API_URL / DSX_RC_API_KEY in .env to add an "
                           "RC-verification provider (VAHAN has no free public API).")


# ================================================================== ANALYTICS
def page_analytics(hero: Callable) -> None:
    safety_db.conn().close()
    hero("Analytics & Heatmaps", "Where and when violations happen, per camera.",
         eyebrow="DriveShieldX · Analytics")
    rv = _q("SELECT detected_at AS ts, rule_type AS kind, camera_id FROM RULE_VIOLATION")
    sp = _q("""SELECT v.violation_time AS ts, 'overspeed_' || v.severity_level AS kind, s.speed_value, s.speed_limit,
                      s.centroid_x, s.centroid_y, s.zone_id, ve.session_id
               FROM VIOLATION v JOIN SPEED_RECORD s ON s.speed_id=v.speed_id
               LEFT JOIN VEHICLE ve ON ve.vehicle_id=s.vehicle_id""")
    events = pd.concat([rv[["ts", "kind"]], sp[["ts", "kind"]]], ignore_index=True)
    if events.empty:
        st.info("No violations recorded yet.")
        return
    events["ts"] = pd.to_datetime(events["ts"], errors="coerce")
    events = events.dropna(subset=["ts"])
    events["weekday"] = events["ts"].dt.day_name().str[:3]
    events["hour"] = events["ts"].dt.hour
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    c1, c2 = st.columns(2)
    with c1:
        grid = events.groupby(["weekday", "hour"]).size().reset_index(name="violations")
        fig = px.density_heatmap(grid, x="hour", y="weekday", z="violations", nbinsx=24,
                                 category_orders={"weekday": order}, color_continuous_scale="Blues",
                                 title="When: violations by weekday and hour")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        by = events.assign(day=events["ts"].dt.date).groupby(["day", "kind"]).size().reset_index(name="count")
        st.plotly_chart(px.bar(by, x="day", y="count", color="kind", title="Violations per day by type"),
                        use_container_width=True)

    st.subheader("Where: over-speed hotspots in the camera view")
    sp_all = _q("""SELECT s.centroid_x AS x, s.centroid_y AS y, s.speed_value, s.speed_limit
                   FROM SPEED_RECORD s WHERE s.centroid_x IS NOT NULL AND s.speed_value > s.speed_limit
                   AND s.speed_value <= 200""")
    if sp_all.empty:
        st.info("No over-speed positions recorded yet.")
    else:
        fig = px.density_heatmap(sp_all, x="x", y="y", nbinsx=40, nbinsy=24, color_continuous_scale="Reds",
                                 title="Image positions of over-speed readings (plausible speeds only)")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)

    geo = pd.DataFrame(safety_db.list_camera_geo())
    if not geo.empty:
        st.subheader("Cameras on the map")
        st.map(geo.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]])
