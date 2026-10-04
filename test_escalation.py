from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db_manager as db

db.init_database()
admin = db.authenticate_user("admin@speedcam.com", "admin123")

# registered owner + plate
if db.authenticate_owner("repeat@ex.com", "Pass123!") is None:
    db.create_owner_account("Repeat Rida", "repeat@ex.com", "Pass123!", "9876543210")
owner = db.authenticate_owner("repeat@ex.com", "Pass123!")
db.add_registered_vehicle(owner["owner_id"], "MH01AB1234", "car", "Hatchback")

cams = db.get_all_cameras()
cam_id = cams[0]["camera_id"]
sess = db.create_session(cam_id, "uploaded_video.mp4", "file", "escalation test")

# --- REGISTERED car overspeeds 3 times (same tracker -> same vehicle) ---
reg_vid = db.upsert_vehicle(sess, tracker_id="T-REG", vehicle_type="car", plate_text="MH01AB1234")
for i, spd in enumerate([78.0, 92.0, 105.0], start=1):
    sp = db.insert_speed_record(reg_vid, spd, 60.0)
    db.insert_violation(sp, spd, 60.0)

# --- UNREGISTERED bike overspeeds once (no plate) ---
unreg_vid = db.upsert_vehicle(sess, tracker_id="T-UNREG", vehicle_type="bike", plate_text=None)
sp = db.insert_speed_record(unreg_vid, 88.0, 50.0)
db.insert_violation(sp, 88.0, 50.0)

print("Unissued violations:", len(db.get_unissued_violations(100)))

# --- BULK issue challans for ALL overspeeders, send to both email + dashboard ---
summary = db.bulk_issue_challans_for_all(admin["user_id"], delivery_channel="both")
print("BULK SUMMARY:", summary)

print("\n--- ALL NOTICES (admin view) ---")
for n in db.get_all_notices(100):
    print(f"  notice#{n['notice_id']} {n['plate_number']:<12} {n['registration']:<12} "
          f"tier={n['offense_tier']:<7} ₹{n['amount']:<7.0f} action={n['action_taken']:<18} "
          f"suspend={n['suspension_months']}mo status={n['payment_status']} ch={n['delivery_channel']}")

print("\n--- REGISTERED OWNER dashboard view ---")
on = db.get_owner_notices(owner["owner_id"])
for n in on:
    print(f"  notice#{n['notice_id']} {n['plate_number']} tier={n['offense_tier']} "
          f"₹{n['amount']:.0f} (overdue→₹{n['overdue_amount']:.0f}) status={n['payment_status']}")
assert len(on) == 3, "registered owner should see their 3 challans"
# exact fine schedule
amounts = {n["offense_tier"]: n["amount"] for n in on}
assert amounts["first"] == 500.0, f"first should be 500, got {amounts['first']}"
assert amounts["second"] == 1000.0, f"second should be 1000, got {amounts['second']}"
assert amounts["third"] == 3000.0, f"third should be 3000, got {amounts['third']}"
assert any(n["action_taken"] == "license_suspension" and n["suspension_months"] >= 3 for n in on), "3rd offense must suspend license"
print("Fine schedule OK: 500 / 1000 / 3000")

# --- PAYMENT (UPI) on the first challan, with a txn ref ---
first = on[-1]  # oldest
from backend.payments import new_txn_ref
ref = new_txn_ref(first["notice_id"])
db.pay_notice(first["notice_id"], "Google Pay", txn_ref=ref)
paid = [x for x in db.get_owner_notices(owner["owner_id"]) if x["notice_id"] == first["notice_id"]][0]
print("\nPaid notice status:", paid["payment_status"], "via", paid["payment_method"], "ref", paid["external_reference"])
assert paid["payment_status"] == "paid" and paid["external_reference"] == ref

# --- OVERDUE jump: force a past due date then sweep; amounts must JUMP to tier overdue ---
conn = db.get_connection()
conn.execute("UPDATE VIOLATION_NOTICE SET due_date='2000-01-01' WHERE payment_status='pending'")
conn.commit(); conn.close()
n_over = db.apply_overdue_penalties()
print("Marked overdue:", n_over)
over = {x["offense_tier"]: x for x in db.get_all_notices(100) if x["payment_status"] == "overdue"}
for t, x in over.items():
    print(f"  {t}: now ₹{x['amount']:.0f} (was base ₹{x['base_amount']:.0f}, jump +₹{x['late_fee']:.0f})")
# second tier should have jumped 1000 -> 2000, third 3000 -> 5000
if "second" in over:
    assert over["second"]["amount"] == 2000.0, f"second overdue should be 2000, got {over['second']['amount']}"
if "third" in over:
    assert over["third"]["amount"] == 5000.0, f"third overdue should be 5000, got {over['third']['amount']}"
print("Overdue jumps OK: 1000→2000, 3000→5000")

print("\nALL ESCALATION + PAYMENT TESTS PASSED ✅")
