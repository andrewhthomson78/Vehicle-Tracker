"""Maintenance schedule templates: JSON files in /schedules, one per engine/model."""

import json
import os
import re

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


def _compact(text):
    """Lower case, letters and digits only, so "MT-07", "MT07" and "mt 07" compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _vehicle_year(vehicle):
    if vehicle.get("year"):
        return int(vehicle["year"])
    first = vehicle.get("first_registered")
    return int(first[:4]) if first and first[:4].isdigit() else None


def _matches(match, vehicle):
    """`model` is one name or a list, matched anywhere in the vehicle's model. Optional
    `years: [from, to]` (either end may be null) keeps e.g. a Mk1 Golf schedule off a modern Golf."""
    if _compact(match.get("make")) != _compact(vehicle.get("make")):
        return False
    models = match.get("model") or ""
    models = [models] if isinstance(models, str) else models
    if not any(_compact(m) in _compact(vehicle.get("model")) for m in models):
        return False
    if match.get("years"):
        lo, hi = match["years"]
        year = _vehicle_year(vehicle)
        if year is None or (lo and year < lo) or (hi and year > hi):
            return False
    return True


def guess_template(vehicle):
    """Best template for a vehicle: a specific match on make/model (and years), else by fuel type."""
    for t in list_templates():
        m = t.get("match")
        if m and _matches(m, vehicle):
            return t["id"]
    fuel = (vehicle.get("fuel") or "").lower()
    if "hybrid" in fuel:
        return "generic-hybrid"
    if "electric" in fuel:
        return "generic-ev"
    if "diesel" in fuel:
        return "generic-diesel"
    return "generic-petrol"
