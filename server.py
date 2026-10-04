"""Vehicle Tracker: MOT, tax, insurance and servicing for all your vehicles.

    python3 server.py          # your real garage (data/garage.db)
    python3 server.py --demo   # made-up vehicles, no API keys needed (data/demo.db)

Then open http://localhost:8765
"""

import json
import os
import re
import sys
import threading
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
PORT = int(os.environ.get("PORT", 8765))


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(ROOT, ".env"))

import base64  # noqa: E402
import secrets  # noqa: E402

from tracker import agent, db, due, garage, govapi, scan, schedules  # noqa: E402

govapi.DEMO = "--demo" in sys.argv
DB_PATH = os.path.join(ROOT, "data", "demo.db" if govapi.DEMO else "garage.db")
DOCS_DIR = os.path.join(ROOT, "data", "documents-demo" if govapi.DEMO else "documents")
DOC_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".pdf": "application/pdf"}
conn = db.connect(DB_PATH)
lock = threading.Lock()
assistant = agent.Assistant(conn, lock)

ROUTES = []


def route(method, pattern):
    def wrap(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return wrap


@route("GET", "/api/dashboard")
def api_dashboard(body):
    return {**garage.dashboard(conn), "assistant": assistant.available()}


@route("POST", "/api/vehicles")
def api_add_vehicle(body):
    return garage.add_vehicle(conn, body, lookup=body.pop("lookup", True))


@route("GET", r"/api/vehicles/(\d+)")
def api_vehicle(body, vid):
    return garage.vehicle_detail(conn, int(vid))


@route("PATCH", r"/api/vehicles/(\d+)")
def api_update_vehicle(body, vid):
    garage._require(conn, int(vid))
    body.pop("reg", None)
    values = {k: (v if v != "" else None) for k, v in body.items()}
    for k in ("service_interval_months", "service_interval_miles"):
        if values.get(k) is None:
            values.pop(k, None)
    db.update_vehicle(conn, int(vid), values)
    return {"ok": True}


@route("DELETE", r"/api/vehicles/(\d+)")
def api_delete_vehicle(body, vid):
    db.delete_vehicle(conn, int(vid))
    return {"ok": True}


@route("POST", r"/api/vehicles/(\d+)/sync")
def api_sync(body, vid):
    return garage.sync_vehicle(conn, int(vid))


@route("POST", r"/api/vehicles/(\d+)/mileage")
def api_add_mileage(body, vid):
    garage._require(conn, int(vid))
    return {"id": db.add_mileage(conn, int(vid), body.get("date") or date.today().isoformat(),
                                 int(body["miles"]), body.get("note"))}


@route("DELETE", r"/api/mileage/(\d+)")
def api_delete_mileage(body, rid):
    db.delete_mileage(conn, int(rid))
    return {"ok": True}


@route("POST", r"/api/vehicles/(\d+)/records")
def api_add_record(body, vid):
    if body.get("miles") in ("", None):
        body["miles"] = None
    rid = garage.log_work(conn, int(vid), body)
    if body.get("document_id"):
        db.link_document(conn, int(body["document_id"]), record_id=rid)
    return {"id": rid}


@route("POST", r"/api/vehicles/(\d+)/scan")
def api_scan(body, vid):
    """Save a photo/PDF of an invoice or MOT certificate, read it, and suggest what to log.
    OCR runs outside the database lock; see Handler._handle."""
    with lock:
        v = garage._require(conn, int(vid))
    raw = body.get("data") or ""
    if "," in raw[:100]:
        raw = raw.split(",", 1)[1]
    blob = base64.b64decode(raw)
    if not blob or len(blob) > 25 * 1024 * 1024:
        raise ValueError("That file is empty or too big (25 MB max)")
    ext = os.path.splitext(body.get("name") or "")[1].lower()
    ext = ext if ext in DOC_TYPES else ".pdf" if blob[:4] == b"%PDF" else ".jpg"
    folder = os.path.join(DOCS_DIR, str(v["id"]))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    try:
        text = scan.ocr(path)
    except Exception as e:  # noqa: BLE001 - keep the photo even if it can't be read
        text = ""
        sys.stderr.write(f"OCR failed: {e}\n")
    with lock:
        parsed = scan.parse(text, v, db.list_items(conn, v["id"]), db.list_vehicles(conn))
        if not text.strip():
            parsed["warnings"].append("Rin couldn't read any text. Try a sharper, flatter photo, or fill it in by hand.")
        doc_id = db.add_document(conn, v["id"], os.path.relpath(path, ROOT), parsed["kind"], text)
    return {"document_id": doc_id, "parsed": parsed, "text": text}


@route("POST", r"/api/vehicles/(\d+)/mot-scan")
def api_mot_scan(body, vid):
    v = garage._require(conn, int(vid))
    test = {"completed_date": body.get("completed_date"), "expiry_date": body.get("expiry_date") or None,
            "result": body.get("result") or "PASSED", "odometer": body.get("odometer") or None,
            "defects": [{"text": t, "type": "ADVISORY", "dangerous": False} for t in body.get("advisories") or [] if t.strip()]}
    if not test["completed_date"]:
        raise ValueError("The test date is needed")
    tid = db.add_mot_test(conn, v["id"], test)
    if test["result"] == "PASSED" and test["expiry_date"] and test["expiry_date"] > (v.get("mot_due") or ""):
        db.update_vehicle(conn, v["id"], {"mot_due": test["expiry_date"], "mot_status": "Valid"})
    if body.get("document_id"):
        db.link_document(conn, int(body["document_id"]), mot_test_id=tid)
    return {"id": tid}


@route("DELETE", r"/api/documents/(\d+)")
def api_delete_document(body, did):
    d = db.get_document(conn, int(did))
    if d:
        full = os.path.realpath(os.path.join(ROOT, d["path"]))
        if full.startswith(os.path.realpath(DOCS_DIR)) and os.path.exists(full):
            os.remove(full)
        db.delete_document(conn, int(did))
    return {"ok": True}


@route("DELETE", r"/api/records/(\d+)")
def api_delete_record(body, rid):
    db.delete_record(conn, int(rid))
    return {"ok": True}


@route("POST", r"/api/vehicles/(\d+)/items")
def api_add_item(body, vid):
    garage._require(conn, int(vid))
    return {"id": db.add_item(conn, int(vid), {**body, "sort": body.get("sort", 999)})}


@route("PATCH", r"/api/items/(\d+)")
def api_update_item(body, iid):
    db.update_item(conn, int(iid), {k: (v if v != "" else None) for k, v in body.items()})
    return {"ok": True}


@route("DELETE", r"/api/items/(\d+)")
def api_delete_item(body, iid):
    db.delete_item(conn, int(iid))
    return {"ok": True}


@route("GET", "/api/version")
def api_version(body):
    """Changes whenever a front-end file changes, so open phone apps know to reload."""
    stamps = [os.path.getmtime(os.path.join(WEB, f)) for f in os.listdir(WEB) if not f.startswith(".")]
    return {"version": str(int(max(stamps)))}


@route("GET", "/api/templates")
def api_templates(body):
    return schedules.list_templates()


@route("POST", r"/api/vehicles/(\d+)/template")
def api_apply_template(body, vid):
    return {"items": garage.apply_template(conn, int(vid), body["template"])}


@route("POST", "/api/chat")
def api_chat(body):
    return assistant.chat(body.get("conversation_id"), body["message"])


@route("POST", "/api/chat/reset")
def api_chat_reset(body):
    assistant.reset(body.get("conversation_id"))
    return {"ok": True}


def calendar_ics():
    """All upcoming deadlines as an iCalendar feed (subscribe or import)."""
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//vehicle-tracker//EN",
             "X-WR-CALNAME:Vehicles", "CALSCALE:GREGORIAN"]
    for v in db.list_vehicles(conn):
        detail = garage.vehicle_detail(conn, v["id"])
        for e in due.upcoming(v, detail["eval"], horizon_days=800):
            d = due.parse_date(e["date"])
            if e["kind"] == "service":
                todo = "; ".join(i["name"] for i in detail["eval"]["next_service_items"])
                desc = f"Needed: {todo}" if todo else ""
            elif e["kind"] == "mot":
                desc = "You can MOT up to a month (minus a day) early and keep the renewal date."
            else:
                desc = ""
            lines += ["BEGIN:VEVENT", f"UID:{e['kind']}-{v['id']}@vehicle-tracker", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{e['title']}: {e['vehicle']} ({v['reg']})",
                      "DESCRIPTION:" + desc.replace(",", "\\,").replace(";", "\\;"),
                      "BEGIN:VALARM", "TRIGGER:-P14D", "ACTION:DISPLAY", f"DESCRIPTION:{e['title']} due",
                      "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


STATIC = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
          ".png": "image/png", ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # request lines only for the API; errors (args[0] is an HTTPStatus) always
        if not args or not isinstance(args[0], str) or "/api/" in args[0]:
            sys.stderr.write("%s\n" % (fmt % args))

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else (
            payload.encode() if isinstance(payload, str) else json.dumps(payload, default=str).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        path = urlparse(self.path).path
        if method == "GET" and path == "/calendar.ics":
            with lock:
                return self._send(200, calendar_ics(), "text/calendar")
        m = re.match(r"^/documents/(\d+)$", path)
        if method == "GET" and m:
            with lock:
                d = db.get_document(conn, int(m.group(1)))
            full = os.path.realpath(os.path.join(ROOT, d["path"])) if d else ""
            if not d or not full.startswith(os.path.realpath(DOCS_DIR)) or not os.path.isfile(full):
                return self._send(404, "Not found", "text/plain")
            with open(full, "rb") as f:
                return self._send(200, f.read(), DOC_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        if method == "GET" and not path.startswith("/api/"):
            return self._static(path)
        for m, pattern, fn in ROUTES:
            match = pattern.match(path)
            if m == method and match:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                    if fn in (api_chat, api_scan):  # long-running; they take the lock themselves
                        return self._send(200, fn(body, *match.groups()))
                    with lock:
                        return self._send(200, fn(body, *match.groups()))
                except garage.NotFound as e:
                    return self._send(404, {"error": str(e)})
                except (ValueError, KeyError, govapi.GovApiError, RuntimeError) as e:
                    return self._send(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "Not found"})

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.realpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._send(404, "Not found", "text/plain")
        with open(full, "rb") as f:
            self._send(200, f.read(), STATIC.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    mode = "DEMO mode (sample data)" if govapi.DEMO else f"database {DB_PATH}"
    print(f"Vehicle Tracker on http://localhost:{PORT}  ({mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
