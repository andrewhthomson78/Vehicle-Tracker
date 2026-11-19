"""Imports work records dropped into a synced folder (for example a Google Drive folder).

This lets something that can't reach your Mac, such as Claude on your phone, add records: it writes a small
JSON file and any photos into the folder, Google Drive syncs them down, and the server imports them.

Folder layout:

    Vehicle Tracker Inbox/
        drk-1254a-honda.json      one job per file (format below)
        DRK-1254A.png             attachments, which several jobs can share
        vehicles.json             written by the tracker: your vehicles, so the sender can match them
        processed/                imported jobs, each with a "result" added, and their attachments
        failed/                   jobs that couldn't be imported, each with an "error" added

Job format (every key except "vehicle" is optional):

    {
      "ref": "DRK 1254A",                       # stops the same job being imported twice
      "vehicle": {"reg": "F20AHT"},             # or {"id": 3}, or {"make": "Honda", "model": "N-Box"}
      "record": {"date": "2026-11-16", "miles": 53461, "kind": "other", "garage": "DRK Imports",
                 "cost": 321.60, "notes": "...", "counts_as_service": 0},
      "mileage": {"date": "2026-11-16", "miles": 53461, "note": "..."},
      "attachments": ["DRK-1254A.png"]
    }
"""

import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime

from . import db

SETTLE_SECONDS = 10      # leave files alone until they've stopped changing (still syncing)
GIVE_UP_SECONDS = 600    # a job that still can't be read or is missing attachments after this goes to failed/
DEFAULT_GLOB = "~/Library/CloudStorage/GoogleDrive-*/My Drive/Vehicle Tracker Inbox"
RESERVED = {"vehicles.json"}


def find_folder():
    """INBOX_DIR from .env, or the Google Drive folder if it exists. None means the inbox is off."""
    configured = os.environ.get("INBOX_DIR", "").strip()
    if configured:
        path = os.path.expanduser(configured)
        return path if os.path.isdir(path) else None
    hits = sorted(glob.glob(os.path.expanduser(DEFAULT_GLOB)))
    return hits[0] if hits else None


def _norm(s):
    return "".join(ch for ch in str(s or "") if ch.isalnum()).lower()


def match_vehicle(vehicles, want):
    if want.get("id") is not None:
        hits = [v for v in vehicles if v["id"] == int(want["id"])]
    elif want.get("reg"):
        hits = [v for v in vehicles if _norm(v["reg"]) == _norm(want["reg"])]
    else:
        make, model = _norm(want.get("make")), _norm(want.get("model"))
        if not make and not model:
            raise ValueError("The job's vehicle needs an id, reg, or make and model")
        hits = [v for v in vehicles if make in _norm(v.get("make"))
                and model in _norm(f"{v.get('model') or ''}{v.get('variant') or ''}")]
    if len(hits) != 1:
        found = ", ".join(f"{v['reg']} ({v.get('make')} {v.get('model')})" for v in hits) or "none"
        raise ValueError(f"Couldn't pick one vehicle for {want} (found {found})")
    return hits[0]


def _settled(path, now):
    return now - os.path.getmtime(path) >= SETTLE_SECONDS


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _move(src, folder):
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(src))
    if os.path.exists(dest):
        stem, ext = os.path.splitext(dest)
        dest = f"{stem}-{datetime.now():%Y%m%d%H%M%S}{ext}"
    shutil.move(src, dest)
    return dest


def write_snapshot(conn, folder):
    vehicles = [{k: v.get(k) for k in ("id", "reg", "nickname", "make", "model", "variant", "year", "colour")}
                for v in db.list_vehicles(conn)]
    path = os.path.join(folder, "vehicles.json")
    new = {"vehicles": vehicles}
    try:
        with open(path) as f:
            if json.load(f).get("vehicles") == vehicles:
                return
    except (OSError, ValueError):
        pass
    _write_json(path, {**new, "updated": datetime.now().isoformat(timespec="seconds")})


def import_job(conn, job, folder, save_document, log_work):
    """Apply one job. Returns a short result dict. Raises ValueError if it can't be applied."""
    v = match_vehicle(db.list_vehicles(conn), job.get("vehicle") or {})
    ref = (job.get("ref") or "").strip()
    if ref:
        for r in db.list_records(conn, v["id"]):
            if ref.lower() in (r.get("notes") or "").lower():
                return {"vehicle": v["reg"], "skipped": f"{ref} is already logged (record {r['id']})"}
    result = {"vehicle": v["reg"], "documents": []}
    doc_ids = []
    for name in job.get("attachments") or []:
        with open(os.path.join(folder, os.path.basename(name)), "rb") as f:
            doc_ids.append(save_document(v, f.read(), name))
    result["documents"] = doc_ids
    rec = job.get("record")
    if rec:
        rec = dict(rec)
        if ref and ref.lower() not in (rec.get("notes") or "").lower():
            rec["notes"] = f"{rec.get('notes') or ''} [{ref}]".strip()
        rid = log_work(conn, v["id"], rec)
        for d in doc_ids:
            db.link_document(conn, d, record_id=rid)
        result["record_id"] = rid
    mil = job.get("mileage")
    if mil and mil.get("miles") not in (None, ""):
        result["mileage_id"] = db.add_mileage(conn, v["id"], mil.get("date") or (rec or {}).get("date"),
                                              int(mil["miles"]), mil.get("note") or ref or None)
    return result


def poll(conn, lock, folder, save_document, log_work, now=None):
    """Import every settled job in the folder. Returns the list of (filename, outcome)."""
    now = now or time.time()
    with lock:
        write_snapshot(conn, folder)
    jobs = sorted(p for p in glob.glob(os.path.join(folder, "*.json"))
                  if os.path.basename(p) not in RESERVED)
    outcomes, done = [], []
    for path in jobs:
        if not _settled(path, now):
            continue
        name = os.path.basename(path)
        try:
            with open(path) as f:
                job = json.load(f)
        except (OSError, ValueError) as e:
            if now - os.path.getmtime(path) < GIVE_UP_SECONDS:
                continue  # probably still syncing
            job = {"error": f"Couldn't read the job: {e}"}
            _write_json(path, job)
            _move(path, os.path.join(folder, "failed"))
            outcomes.append((name, job["error"]))
            continue
        attachments = [os.path.join(folder, os.path.basename(a)) for a in job.get("attachments") or []]
        missing = [a for a in attachments if not os.path.isfile(a) or not _settled(a, now)]
        if missing and now - os.path.getmtime(path) < GIVE_UP_SECONDS:
            continue  # wait for the photos to sync
        try:
            if missing:
                raise ValueError(f"Attachments never arrived: {', '.join(os.path.basename(a) for a in missing)}")
            with lock:
                job["result"] = import_job(conn, job, folder, save_document, log_work)
            dest = "processed"
            outcomes.append((name, job["result"]))
        except Exception as e:  # noqa: BLE001 - one bad job mustn't stop the rest
            job["error"] = f"{type(e).__name__}: {e}"
            dest = "failed"
            outcomes.append((name, job["error"]))
        job["imported_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json(path, job)
        _move(path, os.path.join(folder, dest))
        if dest == "processed":
            done += attachments
    # attachments go with their jobs once nothing waiting still needs them
    still_needed = set()
    for path in glob.glob(os.path.join(folder, "*.json")):
        if os.path.basename(path) in RESERVED:
            continue
        try:
            with open(path) as f:
                still_needed |= {os.path.basename(a) for a in json.load(f).get("attachments") or []}
        except (OSError, ValueError):
            pass
    for a in set(done):
        if os.path.basename(a) not in still_needed and os.path.isfile(a):
            _move(a, os.path.join(folder, "processed"))
    for name, outcome in outcomes:
        sys.stderr.write(f"Inbox: {name}: {outcome}\n")
    return outcomes


def run_forever(conn, lock, save_document, log_work, interval=60):
    announced = None
    while True:
        folder = find_folder()
        if folder != announced:
            print(f"Inbox: watching {folder}" if folder else "Inbox: off (no inbox folder found)")
            announced = folder
        if folder:
            try:
                poll(conn, lock, folder, save_document, log_work)
            except Exception as e:  # noqa: BLE001 - keep watching
                sys.stderr.write(f"Inbox error: {type(e).__name__}: {e}\n")
        time.sleep(interval)
