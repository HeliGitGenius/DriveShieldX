PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS USER (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'traffic_authority')),
    contact_no TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS USER_SECURITY (
    user_id INTEGER PRIMARY KEY,
    totp_secret TEXT,
    is_2fa_enabled INTEGER DEFAULT 0,
    last_verified_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS OWNER_ACCOUNT (
    owner_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    totp_secret TEXT,
    is_2fa_enabled INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS REGISTERED_VEHICLE (
    reg_vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    plate_number TEXT UNIQUE NOT NULL,
    vehicle_type TEXT DEFAULT 'car',
    model_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES OWNER_ACCOUNT(owner_id)
);

CREATE TABLE IF NOT EXISTS CAMERA (
    camera_id INTEGER PRIMARY KEY AUTOINCREMENT,
    location TEXT NOT NULL,
    camera_type TEXT DEFAULT 'CCTV',
    rtsp_url TEXT,
    installation_date DATE,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','inactive','maintenance')),
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS CONFIGURATION (
    config_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    speed_limit REAL NOT NULL DEFAULT 60.0,
    pixel_to_meter_scale REAL NOT NULL DEFAULT 0.045,
    tracker_mode TEXT DEFAULT 'bytetrack' CHECK(tracker_mode IN ('bytetrack','centroid')),
    enable_npr INTEGER DEFAULT 1,
    frame_skip INTEGER DEFAULT 1,
    gpu_enabled INTEGER DEFAULT 0,
    batch_mode INTEGER DEFAULT 0,
    set_by INTEGER,
    set_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camera_id) REFERENCES CAMERA(camera_id),
    FOREIGN KEY (set_by) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS ZONE_CONFIG (
    zone_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    zone_name TEXT NOT NULL,
    x1 INTEGER NOT NULL,
    y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL,
    y2 INTEGER NOT NULL,
    speed_limit REAL NOT NULL,
    pixel_to_meter_scale REAL,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camera_id) REFERENCES CAMERA(camera_id)
);

CREATE TABLE IF NOT EXISTS VIDEO_SESSION (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    video_source TEXT NOT NULL,
    source_type TEXT DEFAULT 'file' CHECK(source_type IN ('file','rtsp','webcam','directory')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','error','stopped')),
    last_frame_no INTEGER DEFAULT 0,
    notes TEXT,
    FOREIGN KEY (camera_id) REFERENCES CAMERA(camera_id)
);

CREATE TABLE IF NOT EXISTS VEHICLE (
    vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_id TEXT,
    vehicle_type TEXT NOT NULL CHECK(vehicle_type IN ('car','bus','truck','bike','unknown')),
    plate_text TEXT,
    ocr_confidence REAL,
    detection_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_time TIMESTAMP,
    session_id INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES VIDEO_SESSION(session_id)
);

CREATE TABLE IF NOT EXISTS SPEED_RECORD (
    speed_id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    zone_id INTEGER,
    speed_value REAL NOT NULL,
    speed_limit REAL NOT NULL,
    calculated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    centroid_x REAL,
    centroid_y REAL,
    source_fps REAL,
    FOREIGN KEY (vehicle_id) REFERENCES VEHICLE(vehicle_id),
    FOREIGN KEY (zone_id) REFERENCES ZONE_CONFIG(zone_id)
);

CREATE TABLE IF NOT EXISTS VIOLATION (
    violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    speed_id INTEGER NOT NULL,
    violation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    severity_level TEXT NOT NULL CHECK(severity_level IN ('low','medium','high','extreme')),
    image_snapshot TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','reviewed','resolved')),
    alert_status TEXT DEFAULT 'queued',
    FOREIGN KEY (speed_id) REFERENCES SPEED_RECORD(speed_id)
);

CREATE TABLE IF NOT EXISTS VIOLATION_NOTICE (
    notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER UNIQUE NOT NULL,
    reg_vehicle_id INTEGER,                       -- nullable: notices are issued for ALL overspeeders
    owner_id INTEGER,                             -- set when the plate matches a registered owner
    plate_number TEXT,                            -- always recorded (registered plate or detected/OCR plate)
    issued_by INTEGER NOT NULL,
    base_amount REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL,
    overdue_amount REAL NOT NULL DEFAULT 0,
    late_fee REAL NOT NULL DEFAULT 0,
    offense_count INTEGER NOT NULL DEFAULT 1,     -- 1 = first time this vehicle overspeeds, etc.
    offense_tier TEXT NOT NULL DEFAULT 'first',   -- first / second / third
    action_taken TEXT NOT NULL DEFAULT 'warning_fine',  -- warning_fine / escalated_fine / license_suspension
    suspension_months INTEGER NOT NULL DEFAULT 0,
    delivery_channel TEXT DEFAULT 'dashboard',    -- dashboard / email / both
    due_date TEXT,
    payment_status TEXT DEFAULT 'pending' CHECK(payment_status IN ('pending','paid','disputed','exported','overdue','waived')),
    payment_method TEXT,
    paid_at TIMESTAMP,
    external_reference TEXT,
    notes TEXT,
    pdf_path TEXT,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (violation_id) REFERENCES VIOLATION(violation_id),
    FOREIGN KEY (issued_by) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS REPORT (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_by INTEGER NOT NULL,
    report_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    report_type TEXT NOT NULL,
    from_date TEXT,
    to_date TEXT,
    file_path TEXT,
    FOREIGN KEY (generated_by) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS REPORT_VIOLATION (
    report_id INTEGER NOT NULL,
    violation_id INTEGER NOT NULL,
    PRIMARY KEY (report_id, violation_id),
    FOREIGN KEY (report_id) REFERENCES REPORT(report_id),
    FOREIGN KEY (violation_id) REFERENCES VIOLATION(violation_id)
);

CREATE TABLE IF NOT EXISTS SYSTEM_EVENT (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER,
    event_type TEXT NOT NULL,
    level TEXT DEFAULT 'info',
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camera_id) REFERENCES CAMERA(camera_id)
);

CREATE TABLE IF NOT EXISTS STREAM_STATUS (
    camera_id INTEGER PRIMARY KEY,
    is_online INTEGER DEFAULT 0,
    last_frame_no INTEGER DEFAULT 0,
    last_fps REAL DEFAULT 0,
    last_latency_ms REAL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camera_id) REFERENCES CAMERA(camera_id)
);

CREATE TABLE IF NOT EXISTS ALERT_OUTBOX (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL CHECK(channel IN ('email','sms','dashboard','push')),
    recipient TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT DEFAULT 'queued' CHECK(status IN ('queued','sent','failed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    error TEXT
);

CREATE TABLE IF NOT EXISTS LOGIN_AUDIT (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('authority','owner')),
    actor_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_vehicle_session ON VEHICLE(session_id);
CREATE INDEX IF NOT EXISTS idx_speed_vehicle ON SPEED_RECORD(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_violation_speed ON VIOLATION(speed_id);
CREATE INDEX IF NOT EXISTS idx_session_camera ON VIDEO_SESSION(camera_id);
CREATE INDEX IF NOT EXISTS idx_config_camera ON CONFIGURATION(camera_id);
CREATE INDEX IF NOT EXISTS idx_notice_vehicle ON VIOLATION_NOTICE(reg_vehicle_id);
CREATE INDEX IF NOT EXISTS idx_owner_email ON OWNER_ACCOUNT(email);
CREATE INDEX IF NOT EXISTS idx_camera_status ON CAMERA(status);
CREATE INDEX IF NOT EXISTS idx_system_event_camera ON SYSTEM_EVENT(camera_id);

-- DriveShieldX layer-2 rule violations (helmet / triple-riding / seat-belt)
CREATE TABLE IF NOT EXISTS RULE_VIOLATION (
    rule_violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    camera_id INTEGER,
    tracker_id TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK(rule_type IN ('no_helmet','three_seater','no_seatbelt')),
    plate_text TEXT,
    reg_vehicle_id INTEGER,
    snapshot_path TEXT,
    fine_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'detected' CHECK(status IN ('detected','issued','paid','disputed')),
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, tracker_id, rule_type)
);
CREATE INDEX IF NOT EXISTS idx_rule_violation_session ON RULE_VIOLATION(session_id);
CREATE INDEX IF NOT EXISTS idx_rule_violation_plate ON RULE_VIOLATION(plate_text);
CREATE INDEX IF NOT EXISTS idx_rule_violation_owner ON RULE_VIOLATION(reg_vehicle_id);


-- Officer review queue fed by the disagreement gate (paper Sec. 4.4).
-- An event lands here when the standalone helmet model and the combined
-- two-wheeler model disagree; NO challan / notice is created for it.
CREATE TABLE IF NOT EXISTS GATE_REVIEW (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    camera_id INTEGER,
    tracker_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    p_standalone REAL,
    p_combined REAL,
    theta REAL,
    snapshot_path TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','confirmed','rejected')),
    reviewer_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, tracker_id, rule_type)
);
CREATE INDEX IF NOT EXISTS idx_gate_review_session ON GATE_REVIEW(session_id);
