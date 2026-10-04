"""Operations shared by the HTTP API and the assistant's tools."""

from datetime import date, datetime

from . import db, due, govapi, schedules


class NotFound(Exception):
    pass


def label(v):
    name = f"{v.get('make') or ''} {v.get('model') or ''}".strip()
    return v.get("nickname") or name or v["reg"]


def _require(conn, vehicle_id):
    v = db.get_vehicle(conn, vehicle_id)
    if not v:
        raise NotFound(f"No vehicle with id {vehicle_id}")
    return v


def _evaluate(conn, v, today=None):
    records = db.list_records(conn, v["id"])
    tests = db.list_mot_tests(conn, v["id"])
    return due.evaluate(v, db.list_items(conn, v["id"]), records, tests,
                        db.list_mileage(conn, v["id"]), today), records, tests


def dashboard(conn):
    vehicles, events = [], []
    for v in db.list_vehicles(conn):
        ev, _, _ = _evaluate(conn, v)
        vehicles.append({**v, "label": label(v), "status": {
            k: ev[k] for k in ("mot", "tax", "insurance", "service", "mileage")},
            "next_service_count": len(ev["next_service_items"])})
        events += due.upcoming(v, ev)
    events.sort(key=lambda e: e["date"])
    return {"vehicles": vehicles, "upcoming": events, "apis": govapi.status()}


def vehicle_detail(conn, vehicle_id):
    v = _require(conn, vehicle_id)
    ev, records, tests = _evaluate(conn, v)
    names = {i["id"]: i["name"] for i in ev["items"]}
    for r in records:
        r["items"] = [names.get(i, "?") for i in r["item_ids"]]
    return {**v, "label": label(v), "eval": ev, "records": records, "mot_tests": tests,
            "mileage_log": db.list_mileage(conn, vehicle_id), "documents": db.list_documents(conn, vehicle_id)}


def add_vehicle(conn, data, lookup=True):
    reg = govapi.normalise_reg(data.get("reg"))
    if not reg:
        raise ValueError("Registration is required")
    if db.find_vehicle_by_reg(conn, reg):
        raise ValueError(f"{reg} is already in your garage")
    values = {k: v for k, v in data.items() if v not in ("", None)}
    values["reg"] = reg
    vid = db.create_vehicle(conn, values)
    warnings = []
    if lookup:
        try:
            warnings = sync_vehicle(conn, vid)["warnings"]
        except govapi.GovApiError as e:
            warnings = [str(e)]
    v = db.get_vehicle(conn, vid)
    template = data.get("template") or schedules.guess_template(v)
    apply_template(conn, vid, template)
    return {"id": vid, "warnings": warnings, "template": template}


def sync_vehicle(conn, vehicle_id):
    v = _require(conn, vehicle_id)
    info = govapi.lookup(v["reg"])
    updates = {k: info[k] for k in ("tax_status", "tax_due", "mot_status", "mot_due", "recall_outstanding")
               if info.get(k)}
    # identity fields only fill blanks, so your own naming wins
    for k in ("make", "model", "colour", "fuel", "engine_cc", "year", "first_registered"):
        if info.get(k) and not v.get(k):
            updates[k] = info[k]
    updates["last_synced"] = datetime.now().isoformat(timespec="seconds")
    updates["sync_sources"] = ", ".join(f"{k.upper()}: {s}" for k, s in info["sources"].items())
    db.update_vehicle(conn, vehicle_id, updates)
    if info["mot_tests"] or info["sources"].get("mot") in ("live", "demo"):
        db.replace_mot_tests(conn, vehicle_id, info["mot_tests"])
    warnings = [f"{k.upper()}: {s}" for k, s in info["sources"].items() if s not in ("live", "demo", "off")]
    return {"warnings": warnings, "sources": info["sources"]}


def apply_template(conn, vehicle_id, template_id, replace=True):
    t = schedules.get_template(template_id)
    if not t:
        raise ValueError(f"Unknown template {template_id}")
    if replace:
        db.clear_items(conn, vehicle_id)
    for n, item in enumerate(t["items"]):
        db.add_item(conn, vehicle_id, {**item, "sort": n, "source": item.get("source") or t["name"]})
    return len(t["items"])


def log_work(conn, vehicle_id, data):
    """Record a service or one-off job. `item_ids` ticks off schedule items."""
    _require(conn, vehicle_id)
    when = data.get("date") or date.today().isoformat()
    due.parse_date(when) or _bad_date(when)
    item_ids = [int(i) for i in data.get("item_ids") or []]
    valid = {i["id"] for i in db.list_items(conn, vehicle_id)}
    unknown = [i for i in item_ids if i not in valid]
    if unknown:
        raise ValueError(f"Schedule item ids {unknown} don't belong to this vehicle")
    rid = db.add_record(conn, vehicle_id, {**data, "date": when}, item_ids)
    return rid


def _bad_date(value):
    raise ValueError(f"Bad date {value!r}, use YYYY-MM-DD")
