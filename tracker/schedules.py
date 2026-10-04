"""Maintenance schedule templates: JSON files in /schedules, one per engine/model."""

import json
import os

SCHEDULE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schedules")


def list_templates():
    out = []
    for name in sorted(os.listdir(SCHEDULE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(SCHEDULE_DIR, name)) as f:
                t = json.load(f)
            out.append({"id": t["id"], "name": t["name"], "source": t.get("source"),
                        "items": t["items"], "match": t.get("match")})
    return out


def get_template(template_id):
    return next((t for t in list_templates() if t["id"] == template_id), None)


def guess_template(vehicle):
    """Best template for a vehicle: a specific match on make/model, else by fuel type."""
    make = (vehicle.get("make") or "").lower()
    model = (vehicle.get("model") or "").lower()
    for t in list_templates():
        m = t.get("match") or {}
        if m and m.get("make", "").lower() == make and m.get("model", "").lower() in model:
            return t["id"]
    fuel = (vehicle.get("fuel") or "").lower()
    if "hybrid" in fuel:
        return "generic-hybrid"
    if "electric" in fuel:
        return "generic-ev"
    if "diesel" in fuel:
        return "generic-diesel"
    return "generic-petrol"
