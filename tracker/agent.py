"""The garage assistant: Claude with tools over the vehicle database.

Ask it questions ("what does the van need at its next service?") or tell it
what you've done ("changed the oil and air filter on the Golf today at 61,200")
and it reads and updates the same data the UI shows. It can also search the web
to research a manufacturer's maintenance schedule and load it into a vehicle.
"""

import json
import os
import uuid
from datetime import date

from . import db, garage, govapi

try:
    import anthropic
except ImportError:  # the rest of the app works without it
    anthropic = None

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
MAX_TURNS = 25

SYSTEM = """You are Rin, the upbeat mechanic mascot of a JDM-style garage app: a personal vehicle tracker used by a UK owner
with several vehicles. Keep the personality light (the odd "yosh!" is fine) and never let it get in the way of accurate information.
You can read and change their data with tools. The UI shows the same data, so anything you change appears there.

How the owner maintains vehicles
- Every vehicle is serviced every 12 months or 5,000 miles, whichever comes first (per-vehicle override in service_interval_*).
- On top of that each vehicle has a maintenance schedule: items with their own time/mileage intervals from the manufacturer
  (spark plugs, cambelt, brake fluid, ...). Items flagged every_service are done at every service.
- get_vehicle returns an evaluation: service due date/mileage, `next_service_items` (what must be done at the next service,
  with reason "due", "early" = would fall overdue before the following service, or "every_service"), MOT/tax/insurance status,
  MOT advisories and estimated current mileage.

When the owner tells you about work they've done
- Work out which vehicle (ask if it's ambiguous) and call get_vehicle to see its schedule item ids.
- Log it with log_work, ticking every schedule item the work covers. A routine service counts_as_service=true;
  a one-off repair (e.g. new tyres, a bulb) is counts_as_service=false unless they say it was a service.
- If they give a mileage, include it. If they don't, log without one and mention that adding it improves forecasts.
- Resolve relative dates ("yesterday", "last Tuesday") against today's date. Never invent costs, garages or mileages.
- Confirm briefly what you logged and what that changes (e.g. the new next-service date).

When asked to research a service schedule
- Use web search to find the manufacturer's maintenance schedule for that exact model, engine and year (UK market).
  Prefer manufacturer handbooks/service booklets and dealer sources; say which you used.
- Convert it into schedule items (interval_months / interval_miles; omit an interval if the maker doesn't specify one).
  Don't add oil & filter intervals from the maker. The owner's 12 months / 5,000 miles rule covers them as every_service items.
- Show the proposed schedule and get a yes before calling set_schedule, since it replaces the existing one.

General
- Be concise, like a knowledgeable mechanic friend. Use dates like "14 Mar 2027" and miles with commas.
- Ask before deleting anything. Road tax, MOT and history come from DVLA/DVSA via sync_vehicle, and insurance is entered by hand.
- You're not a replacement for a mechanic's inspection. Flag safety-relevant MOT advisories (tyres, brakes) when relevant.
"""

TOOLS = [
    {
        "name": "list_vehicles",
        "description": "List every vehicle with id, reg, name, estimated mileage and MOT/tax/insurance/service status. Start here.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_vehicle",
        "description": "Full detail for one vehicle: details, schedule items (with ids and next due), what's needed at the next service, service history, MOT history and advisories, mileage readings.",
        "input_schema": {"type": "object", "properties": {"vehicle_id": {"type": "integer"}},
                         "required": ["vehicle_id"]},
    },
    {
        "name": "log_work",
        "description": "Record work done on a vehicle (a service or a one-off job). Tick off schedule items via item_ids.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle_id": {"type": "integer"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "miles": {"type": "integer", "description": "Odometer reading at the time, if known"},
                "kind": {"type": "string", "enum": ["service", "interim", "full", "major", "repair", "tyres", "mot-work", "other"]},
                "counts_as_service": {"type": "boolean", "description": "True if this resets the 12 month / 5,000 mile service clock"},
                "item_ids": {"type": "array", "items": {"type": "integer"}, "description": "Schedule item ids this work covers"},
                "garage": {"type": "string"},
                "cost": {"type": "number", "description": "GBP"},
                "notes": {"type": "string", "description": "What was done, in plain words"},
            },
            "required": ["vehicle_id", "date", "kind", "counts_as_service", "notes"],
        },
    },
    {
        "name": "delete_work_record",
        "description": "Delete a service/work record by its id (only after the owner confirms).",
        "input_schema": {"type": "object", "properties": {"record_id": {"type": "integer"}},
                         "required": ["record_id"]},
    },
    {
        "name": "record_mileage",
        "description": "Store an odometer reading.",
        "input_schema": {"type": "object", "properties": {
            "vehicle_id": {"type": "integer"}, "date": {"type": "string", "description": "YYYY-MM-DD"},
            "miles": {"type": "integer"}}, "required": ["vehicle_id", "date", "miles"]},
    },
    {
        "name": "update_vehicle",
        "description": "Change vehicle fields, e.g. nickname, insurance_provider, insurance_policy, insurance_due (YYYY-MM-DD), mot_due, tax_due, service_interval_months, service_interval_miles, annual_miles, model, variant, notes.",
        "input_schema": {"type": "object", "properties": {
            "vehicle_id": {"type": "integer"},
            "fields": {"type": "object", "description": "Field name -> new value"}},
            "required": ["vehicle_id", "fields"]},
    },
    {
        "name": "add_vehicle",
        "description": "Add a vehicle by UK registration. Looks it up with DVLA/DVSA and applies a generic schedule for its fuel type.",
        "input_schema": {"type": "object", "properties": {
            "reg": {"type": "string"}, "nickname": {"type": "string"}}, "required": ["reg"]},
    },
    {
        "name": "sync_vehicle",
        "description": "Refresh tax status, MOT expiry and full MOT history from DVLA/DVSA.",
        "input_schema": {"type": "object", "properties": {"vehicle_id": {"type": "integer"}},
                         "required": ["vehicle_id"]},
    },
    {
        "name": "set_schedule",
        "description": "Replace a vehicle's whole maintenance schedule (e.g. with researched manufacturer data). Past work records keep their dates but lose links to removed items.",
        "input_schema": {"type": "object", "properties": {
            "vehicle_id": {"type": "integer"},
            "source": {"type": "string", "description": "Where the schedule came from (document/site, date)"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "interval_months": {"type": "integer"},
                "interval_miles": {"type": "integer"},
                "every_service": {"type": "boolean"},
                "notes": {"type": "string"}}, "required": ["name"]}}},
            "required": ["vehicle_id", "source", "items"]},
    },
    {
        "name": "update_schedule_item",
        "description": "Edit or add one schedule item. Omit item_id to add. baseline_date/baseline_miles record when it was last done before this app existed.",
        "input_schema": {"type": "object", "properties": {
            "vehicle_id": {"type": "integer"},
            "item_id": {"type": "integer"},
            "name": {"type": "string"},
            "interval_months": {"type": ["integer", "null"]},
            "interval_miles": {"type": ["integer", "null"]},
            "every_service": {"type": "boolean"},
            "notes": {"type": "string"},
            "baseline_date": {"type": "string"},
            "baseline_miles": {"type": "integer"}},
            "required": ["vehicle_id"]},
    },
    {
        "name": "delete_schedule_item",
        "description": "Remove a schedule item (e.g. cambelt on a chain engine).",
        "input_schema": {"type": "object", "properties": {"item_id": {"type": "integer"}},
                         "required": ["item_id"]},
    },
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
]


def _compact_detail(d):
    ev = d["eval"]
    return {
        "vehicle": {k: d.get(k) for k in (
            "id", "reg", "label", "nickname", "make", "model", "variant", "year", "fuel", "engine_cc",
            "first_registered", "mot_due", "tax_due", "tax_status", "insurance_provider", "insurance_due",
            "service_interval_months", "service_interval_miles", "annual_miles", "recall_outstanding", "notes")},
        "mileage": ev["mileage"],
        "mot": ev["mot"], "tax": ev["tax"], "insurance": ev["insurance"], "service": ev["service"],
        "next_service_items": [{"id": i["id"], "name": i["name"], "reason": i["reason"],
                                "due": i["due"]} for i in ev["next_service_items"]],
        "schedule": [{"id": i["id"], "name": i["name"], "interval_months": i["interval_months"],
                      "interval_miles": i["interval_miles"], "every_service": bool(i["every_service"]),
                      "last_done": i["last_done"], "basis": i["basis"], "due": i["due"], "notes": i["notes"]}
                     for i in ev["items"]],
        "work_history": [{k: r.get(k) for k in ("id", "date", "miles", "kind", "garage", "cost", "notes",
                                                 "counts_as_service", "items")} for r in d["records"]],
        "latest_mot_advisories": ev["latest_mot_advisories"],
        "mot_history": [{k: t[k] for k in ("completed_date", "result", "odometer", "expiry_date")}
                        for t in d["mot_tests"][:6]],
        "mileage_readings": d["mileage_log"][:10],
    }


def run_tool(conn, name, args):
    if name == "list_vehicles":
        data = garage.dashboard(conn)
        return [{"id": v["id"], "reg": v["reg"], "name": v["label"], "fuel": v.get("fuel"),
                 "estimated_miles": v["status"]["mileage"]["estimated_now"],
                 **{k: {"status": v["status"][k]["status"],
                        "date": v["status"][k].get("date") or v["status"][k].get("projected_date")}
                    for k in ("mot", "tax", "insurance", "service")}}
                for v in data["vehicles"]]
    if name == "get_vehicle":
        return _compact_detail(garage.vehicle_detail(conn, args["vehicle_id"]))
    if name == "log_work":
        rid = garage.log_work(conn, args["vehicle_id"], args)
        ev = garage.vehicle_detail(conn, args["vehicle_id"])["eval"]
        return {"record_id": rid, "next_service": ev["service"]}
    if name == "delete_work_record":
        if not db.get_record(conn, args["record_id"]):
            raise garage.NotFound("No such record")
        db.delete_record(conn, args["record_id"])
        return {"deleted": args["record_id"]}
    if name == "record_mileage":
        garage._require(conn, args["vehicle_id"])
        return {"id": db.add_mileage(conn, args["vehicle_id"], args["date"], int(args["miles"]))}
    if name == "update_vehicle":
        garage._require(conn, args["vehicle_id"])
        fields = {k: v for k, v in args["fields"].items() if k in db.VEHICLE_FIELDS and k != "reg"}
        db.update_vehicle(conn, args["vehicle_id"], fields)
        return {"updated": sorted(fields)}
    if name == "add_vehicle":
        return garage.add_vehicle(conn, {"reg": args["reg"], "nickname": args.get("nickname")})
    if name == "sync_vehicle":
        return garage.sync_vehicle(conn, args["vehicle_id"])
    if name == "set_schedule":
        garage._require(conn, args["vehicle_id"])
        db.clear_items(conn, args["vehicle_id"])
        for n, item in enumerate(args["items"]):
            db.add_item(conn, args["vehicle_id"], {**item, "sort": n, "source": args["source"]})
        return {"items": len(args["items"])}
    if name == "update_schedule_item":
        fields = {k: v for k, v in args.items() if k in db.ITEM_FIELDS}
        if args.get("item_id"):
            item = db.get_item(conn, args["item_id"])
            if not item or item["vehicle_id"] != args["vehicle_id"]:
                raise garage.NotFound("No such schedule item on that vehicle")
            db.update_item(conn, args["item_id"], fields)
            return {"updated": args["item_id"]}
        if not fields.get("name"):
            raise ValueError("name is required to add an item")
        return {"added": db.add_item(conn, args["vehicle_id"], {**fields, "sort": 999, "source": "Assistant"})}
    if name == "delete_schedule_item":
        if not db.get_item(conn, args["item_id"]):
            raise garage.NotFound("No such schedule item")
        db.delete_item(conn, args["item_id"])
        return {"deleted": args["item_id"]}
    raise ValueError(f"Unknown tool {name}")


def _describe(name, args, result):
    """One-line note for the UI about what a tool call did."""
    if name == "log_work":
        return f"Logged {args.get('kind')} on {args.get('date')}: {args.get('notes')}"
    if name == "record_mileage":
        return f"Recorded {int(args['miles']):,} mi on {args['date']}"
    if name == "update_vehicle":
        return "Updated " + ", ".join(sorted(args.get("fields", {})))
    if name == "set_schedule":
        return f"Replaced schedule with {len(args['items'])} items"
    if name in ("add_vehicle", "sync_vehicle", "delete_work_record", "update_schedule_item", "delete_schedule_item"):
        return name.replace("_", " ").capitalize()
    return None


class Assistant:
    def __init__(self, conn, lock):
        self.conn = conn
        self.lock = lock
        self.conversations = {}
        self.client = None

    def available(self):
        if anthropic is None:
            return {"ok": False, "reason": "Install the SDK: pip3 install anthropic"}
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return {"ok": False, "reason": "Set ANTHROPIC_API_KEY in .env"}
        return {"ok": True, "model": MODEL}

    def chat(self, conversation_id, text):
        status = self.available()
        if not status["ok"]:
            raise RuntimeError(status["reason"])
        if self.client is None:
            self.client = anthropic.Anthropic()
        conversation_id = conversation_id or uuid.uuid4().hex
        messages = self.conversations.setdefault(conversation_id, [])
        messages.append({"role": "user", "content": text})
        system = SYSTEM + f"\nToday is {date.today().strftime('%A %d %B %Y')}." + (
            "\nThe app is in DEMO mode with made-up vehicles." if govapi.DEMO else "")

        actions, changed = [], False
        for _ in range(MAX_TURNS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return self._reply(conversation_id, "Sorry, I can't help with that one.", actions, changed)
            if response.stop_reason == "pause_turn":  # long web search; let it carry on
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                reply = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    reply += "\n\n(Reply was cut off.)"
                return self._reply(conversation_id, reply, actions, changed)

            results = []
            for block in tool_uses:
                try:
                    with self.lock:
                        result = run_tool(self.conn, block.name, block.input or {})
                    note = _describe(block.name, block.input or {}, result)
                    if note:
                        actions.append(note)
                        changed = True
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(result, default=str)})
                except (garage.NotFound, ValueError, KeyError, TypeError, govapi.GovApiError) as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})

        return self._reply(conversation_id, "I stopped after too many steps. Try a narrower request.",
                           actions, changed)

    def _reply(self, conversation_id, text, actions, changed):
        return {"conversation_id": conversation_id, "reply": text, "actions": actions, "changed": changed}

    def reset(self, conversation_id):
        self.conversations.pop(conversation_id, None)
