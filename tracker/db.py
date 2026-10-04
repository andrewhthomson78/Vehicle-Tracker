"""SQLite storage. Every function takes an open connection and returns plain dicts."""

import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY,
    reg TEXT NOT NULL UNIQUE,
    nickname TEXT,
    make TEXT, model TEXT, variant TEXT,
    year INTEGER, fuel TEXT, engine_cc INTEGER, colour TEXT,
    first_registered TEXT,
    mot_due TEXT, mot_status TEXT,
    tax_due TEXT, tax_status TEXT,
    insurance_provider TEXT, insurance_policy TEXT, insurance_due TEXT,
    service_interval_months INTEGER NOT NULL DEFAULT 12,
    service_interval_miles INTEGER NOT NULL DEFAULT 5000,
    annual_miles INTEGER,
    recall_outstanding TEXT,
    notes TEXT,
    last_synced TEXT,
    sync_sources TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mileage_log (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    miles INTEGER NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS mot_tests (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    test_number TEXT,
    completed_date TEXT,
    result TEXT,
    expiry_date TEXT,
    odometer INTEGER,
    defects TEXT
);

CREATE TABLE IF NOT EXISTS schedule_items (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    interval_months INTEGER,
    interval_miles INTEGER,
    every_service INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    source TEXT,
    baseline_date TEXT,
    baseline_miles INTEGER,
    sort INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS service_records (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    miles INTEGER,
    kind TEXT NOT NULL DEFAULT 'service',
    garage TEXT,
    cost REAL,
    notes TEXT,
    counts_as_service INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    kind TEXT,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ocr_text TEXT,
    record_id INTEGER REFERENCES service_records(id) ON DELETE SET NULL,
    mot_test_id INTEGER
);

CREATE TABLE IF NOT EXISTS service_record_items (
    record_id INTEGER NOT NULL REFERENCES service_records(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES schedule_items(id) ON DELETE CASCADE,
    PRIMARY KEY (record_id, item_id)
);
"""

VEHICLE_FIELDS = [
    "reg", "nickname", "make", "model", "variant", "year", "fuel", "engine_cc", "colour",
    "first_registered", "mot_due", "mot_status", "tax_due", "tax_status",
    "insurance_provider", "insurance_policy", "insurance_due",
    "service_interval_months", "service_interval_miles", "annual_miles",
    "recall_outstanding", "notes", "last_synced", "sync_sources", "bay",
    "tyre_front_psi", "tyre_rear_psi", "tyre_front_laden_psi", "tyre_rear_laden_psi", "tyre_size", "tyre_note",
]
ITEM_FIELDS = [
    "name", "interval_months", "interval_miles", "every_service", "notes", "source",
    "baseline_date", "baseline_miles", "sort",
]
RECORD_FIELDS = ["date", "miles", "kind", "garage", "cost", "notes", "counts_as_service"]


def connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vehicles)")}
    added = {"bay": "INTEGER",  # the order vehicles appear in
             "tyre_front_psi": "REAL", "tyre_rear_psi": "REAL",
             "tyre_front_laden_psi": "REAL", "tyre_rear_laden_psi": "REAL",
             "tyre_size": "TEXT", "tyre_note": "TEXT"}
    for col, typ in added.items():  # columns added after launch
        if col not in cols:
            conn.execute(f"ALTER TABLE vehicles ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _pick(data, fields):
    return {k: data[k] for k in fields if k in data}


def _insert(conn, table, values):
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    return cur.lastrowid


def _update(conn, table, row_id, values):
    if not values:
        return
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])


# vehicles

def list_vehicles(conn):
    return _rows(conn.execute("SELECT * FROM vehicles ORDER BY bay IS NULL, bay, COALESCE(nickname, reg)"))


def get_vehicle(conn, vehicle_id):
    row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    return dict(row) if row else None


def find_vehicle_by_reg(conn, reg):
    row = conn.execute("SELECT * FROM vehicles WHERE reg = ?", (reg,)).fetchone()
    return dict(row) if row else None


def create_vehicle(conn, data):
    values = _pick(data, VEHICLE_FIELDS)
    values.setdefault("bay", conn.execute("SELECT COALESCE(MAX(bay), 0) + 1 FROM vehicles").fetchone()[0])
    vid = _insert(conn, "vehicles", values)
    conn.commit()
    return vid


def update_vehicle(conn, vehicle_id, data):
    _update(conn, "vehicles", vehicle_id, _pick(data, VEHICLE_FIELDS))
    conn.commit()


def delete_vehicle(conn, vehicle_id):
    conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    conn.commit()


# mileage

def list_mileage(conn, vehicle_id):
    return _rows(conn.execute(
        "SELECT * FROM mileage_log WHERE vehicle_id = ? ORDER BY date DESC", (vehicle_id,)))


def add_mileage(conn, vehicle_id, date, miles, note=None):
    rid = _insert(conn, "mileage_log",
                  {"vehicle_id": vehicle_id, "date": date, "miles": miles, "note": note})
    conn.commit()
    return rid


def delete_mileage(conn, row_id):
    conn.execute("DELETE FROM mileage_log WHERE id = ?", (row_id,))
    conn.commit()


# MOT tests (replaced wholesale on each sync)

def list_mot_tests(conn, vehicle_id):
    tests = _rows(conn.execute(
        "SELECT * FROM mot_tests WHERE vehicle_id = ? ORDER BY completed_date DESC", (vehicle_id,)))
    for t in tests:
        t["defects"] = json.loads(t["defects"] or "[]")
    return tests


def replace_mot_tests(conn, vehicle_id, tests):
    conn.execute("DELETE FROM mot_tests WHERE vehicle_id = ?", (vehicle_id,))
    for t in tests:
        _insert(conn, "mot_tests", {
            "vehicle_id": vehicle_id,
            "test_number": t.get("test_number"),
            "completed_date": t.get("completed_date"),
            "result": t.get("result"),
            "expiry_date": t.get("expiry_date"),
            "odometer": t.get("odometer"),
            "defects": json.dumps(t.get("defects") or []),
        })
    conn.commit()


# schedule items

def list_items(conn, vehicle_id):
    return _rows(conn.execute(
        "SELECT * FROM schedule_items WHERE vehicle_id = ? ORDER BY sort, id", (vehicle_id,)))


def get_item(conn, item_id):
    row = conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def add_item(conn, vehicle_id, data):
    values = _pick(data, ITEM_FIELDS)
    values["vehicle_id"] = vehicle_id
    values["every_service"] = 1 if values.get("every_service") else 0
    rid = _insert(conn, "schedule_items", values)
    conn.commit()
    return rid


def update_item(conn, item_id, data):
    values = _pick(data, ITEM_FIELDS)
    if "every_service" in values:
        values["every_service"] = 1 if values["every_service"] else 0
    _update(conn, "schedule_items", item_id, values)
    conn.commit()


def delete_item(conn, item_id):
    conn.execute("DELETE FROM schedule_items WHERE id = ?", (item_id,))
    conn.commit()


def clear_items(conn, vehicle_id):
    conn.execute("DELETE FROM schedule_items WHERE vehicle_id = ?", (vehicle_id,))
    conn.commit()


# service records

def list_records(conn, vehicle_id):
    records = _rows(conn.execute(
        "SELECT * FROM service_records WHERE vehicle_id = ? ORDER BY date DESC, id DESC",
        (vehicle_id,)))
    links = conn.execute(
        "SELECT sri.record_id, sri.item_id FROM service_record_items sri "
        "JOIN service_records sr ON sr.id = sri.record_id WHERE sr.vehicle_id = ?",
        (vehicle_id,)).fetchall()
    by_record = {}
    for rec_id, item_id in links:
        by_record.setdefault(rec_id, []).append(item_id)
    for r in records:
        r["item_ids"] = by_record.get(r["id"], [])
    return records


def get_record(conn, record_id):
    row = conn.execute("SELECT * FROM service_records WHERE id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def add_record(conn, vehicle_id, data, item_ids):
    values = _pick(data, RECORD_FIELDS)
    values["vehicle_id"] = vehicle_id
    values["counts_as_service"] = 0 if values.get("counts_as_service") in (0, False) else 1
    rid = _insert(conn, "service_records", values)
    for item_id in set(item_ids or []):
        conn.execute("INSERT INTO service_record_items (record_id, item_id) VALUES (?, ?)",
                     (rid, item_id))
    conn.commit()
    return rid


def delete_record(conn, record_id):
    conn.execute("DELETE FROM service_records WHERE id = ?", (record_id,))
    conn.commit()


# documents (photos/PDFs of invoices and MOT certificates)

def add_document(conn, vehicle_id, path, kind, ocr_text):
    rid = _insert(conn, "documents", {"vehicle_id": vehicle_id, "path": path, "kind": kind, "ocr_text": ocr_text})
    conn.commit()
    return rid


def get_document(conn, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_documents(conn, vehicle_id):
    return _rows(conn.execute(
        "SELECT id, kind, uploaded_at, record_id, mot_test_id FROM documents WHERE vehicle_id = ? ORDER BY id DESC",
        (vehicle_id,)))


def link_document(conn, doc_id, **fields):
    _update(conn, "documents", doc_id, {k: v for k, v in fields.items() if k in ("record_id", "mot_test_id", "kind")})
    conn.commit()


def delete_document(conn, doc_id):
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()


def add_mot_test(conn, vehicle_id, test):
    rid = _insert(conn, "mot_tests", {
        "vehicle_id": vehicle_id, "test_number": test.get("test_number") or "scanned",
        "completed_date": test.get("completed_date"), "result": test.get("result"),
        "expiry_date": test.get("expiry_date"), "odometer": test.get("odometer"),
        "defects": json.dumps(test.get("defects") or []),
    })
    conn.commit()
    return rid
