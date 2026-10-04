from __future__ import annotations

import json
from pathlib import Path

from database.db_manager import (
    add_camera,
    add_registered_vehicle,
    add_user,
    authenticate_owner,
    authenticate_user,
    create_notice,
    create_session,
    create_owner_account,
    generate_totp_secret,
    get_all_cameras,
    get_all_notices,
    get_config,
    get_system_health,
    get_unissued_violations,
    get_user_security,
    get_violation_stats,
    init_database,
    insert_speed_record,
    insert_vehicle,
    insert_violation,
    set_user_totp_secret,
    verify_totp,
)


def main() -> None:
    init_database()
    assert authenticate_user("admin@speedcam.com", "admin123") is not None
    admin = authenticate_user("admin@speedcam.com", "admin123")
    sec = get_user_security(admin["user_id"])
    secret = sec.get("totp_secret") or generate_totp_secret()
    set_user_totp_secret(admin["user_id"], secret, True)
    code = None
    # brute-force current code via helper semantics impossible here, so just ensure verifier accepts correct shape when secret exists using internal function output if available.
    from database.db_manager import _totp_at

    code = _totp_at(secret, int(__import__("time").time() // 30))
    assert verify_totp(secret, code)

    if authenticate_owner("smoke@example.com", "Test123!") is None:
        create_owner_account("Smoke Owner", "smoke@example.com", "Test123!", "9000000000")
    owner = authenticate_owner("smoke@example.com", "Test123!")
    assert owner is not None
    add_registered_vehicle(owner["owner_id"], "MH00SMOKE1", "car", "Sedan")

    cameras = get_all_cameras()
    assert len(cameras) >= 1
    cfg = get_config(cameras[0]["camera_id"]) if isinstance(cameras[0], dict) else get_config(cameras[0]["camera_id"])
    assert cfg is not None

    # seed one speed/violation if DB is empty
    session_id = create_session(cameras[0]["camera_id"] if isinstance(cameras[0], dict) else cameras[0]["camera_id"], "smoke-test", "file", "smoke")
    vid = insert_vehicle("car", session_id=session_id)
    speed_id = insert_speed_record(vid, 78.0, 60.0)
    vio_id = insert_violation(speed_id, 78.0, 60.0, None)

    stats = get_violation_stats()
    health = get_system_health()
    print(json.dumps({"stats": stats, "health_keys": list(health.keys()), "sample_violation_id": vio_id}, indent=2))


if __name__ == "__main__":
    main()
