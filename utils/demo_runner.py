"""
Demo runner: simulates detection pipeline with synthetic frame data
to verify the full DB pipeline works correctly.
Run: python utils/demo_runner.py
"""

import sys, os
import numpy as np
import random
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import (
    init_database, create_session, close_session,
    insert_vehicle, insert_speed_record, insert_violation,
    get_violation_stats, get_all_violations, get_config
)
from detection.centroid_tracker import CentroidTracker
from detection.speed_estimator import SpeedEstimator

SPEED_LIMIT = 60.0
PIXEL_TO_METER = 0.045
FPS = 25.0
NUM_FRAMES = 200
NUM_VEHICLES = 5

def simulate():
    print("=" * 55)
    print("  Overspeed Detection — DB Pipeline Simulation")
    print("=" * 55)

    init_database()
    session_id = create_session(camera_id=1, video_source="DEMO_SYNTHETIC")
    print(f"[DB] Session created: {session_id}")

    tracker = CentroidTracker(max_disappeared=30, max_distance=100)
    estimator = SpeedEstimator(fps=FPS, pixel_to_meter=PIXEL_TO_METER)

    # Simulate vehicle paths
    vehicles = []
    for i in range(NUM_VEHICLES):
        # Vary speeds: some overspeeding
        target_speed_kmh = random.choice([40, 55, 65, 75, 90, 110])
        # Convert to pixels/frame: speed_kmh → m/s → px/frame
        speed_ms = target_speed_kmh / 3.6
        px_per_frame = speed_ms / PIXEL_TO_METER / FPS
        vehicles.append({
            "x": random.randint(50, 150),
            "y": random.randint(100 + i * 80, 150 + i * 80),
            "px_per_frame": px_per_frame,
            "type": random.choice(["car", "car", "bus", "truck"]),
            "target_speed": target_speed_kmh
        })

    vehicle_db_ids = {}
    logged_violations = set()
    total_violations = 0

    print(f"\n[Sim] Running {NUM_FRAMES} frames with {NUM_VEHICLES} vehicles...")
    print(f"      Speed limit: {SPEED_LIMIT} km/h\n")

    for frame_num in range(NUM_FRAMES):
        # Move vehicles
        rects = []
        vtypes = []
        for v in vehicles:
            v["x"] += v["px_per_frame"] + random.gauss(0, 0.5)
            x1 = int(v["x"])
            y1 = int(v["y"])
            x2 = x1 + 60
            y2 = y1 + 30
            rects.append((x1, y1, x2, y2))
            vtypes.append(v["type"])

        # Track
        objects = tracker.update(rects, vtypes)

        # Speed + DB
        for obj_id, centroid in objects.items():
            positions = tracker.get_recent_positions(obj_id, n=8)
            speed = estimator.estimate_speed(positions, vehicle_id=obj_id)
            if speed is None:
                continue

            if obj_id not in vehicle_db_ids:
                v_type = tracker.vehicle_types.get(obj_id, "car")
                db_vid = insert_vehicle(v_type, session_id)
                vehicle_db_ids[obj_id] = db_vid

            if frame_num % 10 == 0:
                db_vid = vehicle_db_ids[obj_id]
                speed_id = insert_speed_record(db_vid, speed, SPEED_LIMIT)

                if speed > SPEED_LIMIT and obj_id not in logged_violations:
                    viol_id = insert_violation(speed_id, speed, SPEED_LIMIT, None)
                    logged_violations.add(obj_id)
                    total_violations += 1
                    print(f"  🚨 VIOLATION | V{obj_id} | {speed:.1f} km/h | "
                          f"Limit: {SPEED_LIMIT} | "
                          f"Type: {tracker.vehicle_types.get(obj_id,'car')}")

    close_session(session_id)

    # Final stats
    print("\n" + "=" * 55)
    print("  SIMULATION COMPLETE — DATABASE SUMMARY")
    print("=" * 55)
    stats = get_violation_stats()
    print(f"  Total violations:  {stats['total']}")
    print(f"  Today's count:     {stats['today']}")
    print(f"  Average speed:     {stats['avg_speed']} km/h")
    print(f"  By severity:       {stats['by_severity']}")

    violations = get_all_violations(20)
    print(f"\n  Recent violations ({len(violations)}):")
    for v in violations[:5]:
        print(f"    ViolID={v['violation_id']} | V{v['vehicle_id']} {v['vehicle_type']} | "
              f"{v['speed_value']} km/h | {v['severity_level']} | {v['violation_time']}")

    print("\n  [OK] Database is populated. Launch dashboard:")
    print("       streamlit run dashboard/app.py")
    print("=" * 55)


if __name__ == "__main__":
    simulate()
