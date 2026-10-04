"""Works out what is due when, for one vehicle.

Mileage-based deadlines are turned into dates using the vehicle's average usage,
learned from every odometer reading we have (manual entries, service records and
MOT tests). Services use "whichever comes first" of the time and mileage limits.
"""

import calendar
from datetime import date, timedelta

DEFAULT_ANNUAL_MILES = 7000
STALE_DAYS = 548  # readings older than ~18 months can't be projected forward
SOON_DAYS = 30
SOON_MILES = 500


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def iso(d):
    return d.isoformat() if d else None


class MileageModel:
    """Estimates the odometer on any date from past readings."""

    def __init__(self, readings, annual_miles, today):
        pts = sorted((d, m) for d, m in readings if d and m is not None)
        # drop readings that go backwards (typos, clocked or swapped clusters)
        clean = []
        for d, m in pts:
            if clean and m < clean[-1][1]:
                continue
            clean.append((d, m))
        self.last = clean[-1] if clean else None
        self.readings = clean

        rate, basis = None, "default"
        if annual_miles:
            rate, basis = annual_miles / 365.25, "set"
        elif len(clean) >= 2:
            recent = [p for p in clean if (self.last[0] - p[0]).days <= 3 * 365]
            first = recent[0] if len(recent) >= 2 else clean[0]
            days = (self.last[0] - first[0]).days
            if days >= 60 and self.last[1] > first[1]:
                rate, basis = (self.last[1] - first[1]) / days, "history"
        if rate is None:
            rate = DEFAULT_ANNUAL_MILES / 365.25
        self.rate = rate
        self.basis = basis
        self.today = today
        # with an old last reading (e.g. a car laid up for years) any projection is fiction
        self.stale = not self.last or (today - self.last[0]).days > STALE_DAYS

    def at(self, d):
        if not self.last or (self.stale and d > self.last[0]):
            return None
        return round(self.last[1] + self.rate * max(0, (d - self.last[0]).days))

    @property
    def now(self):
        return self.at(self.today)

    def date_when(self, miles):
        if not self.last or miles is None or self.stale:
            return None
        days = (miles - self.last[1]) / self.rate
        return self.last[0] + timedelta(days=round(days))

    def summary(self):
        return {
            "estimated_now": self.now,
            "annual_miles": round(self.rate * 365.25),
            "basis": self.basis,
            "stale": self.stale,
            "last_reading": {"date": iso(self.last[0]), "miles": self.last[1]} if self.last else None,
        }


def interval_due(base_date, base_miles, months, miles, model, today):
    """Next due point for something done at (base_date, base_miles) every months/miles."""
    due_date = add_months(base_date, months) if months and base_date else None
    due_miles = base_miles + miles if miles and base_miles is not None else None
    by_miles_date = model.date_when(due_miles) if due_miles is not None else None

    options = [(d, why) for d, why in ((due_date, "time"), (by_miles_date, "mileage")) if d]
    if not options:
        return None
    effective, trigger = min(options, key=lambda o: o[0])

    est_now = model.now
    miles_left = due_miles - est_now if due_miles is not None and est_now is not None else None
    days_left = (effective - today).days
    if (due_date and due_date < today) or (miles_left is not None and miles_left <= 0):
        status = "overdue"
    elif days_left <= SOON_DAYS or (miles_left is not None and miles_left <= SOON_MILES):
        status = "soon"
    else:
        status = "ok"
    return {
        "due_date": iso(due_date),
        "due_miles": due_miles,
        "projected_date": iso(effective),
        "trigger": trigger,
        "days_left": days_left,
        "miles_left": miles_left,
        "status": status,
    }


def date_status(value, today, soon_days=SOON_DAYS):
    d = parse_date(value)
    if not d:
        return {"date": None, "days_left": None, "status": "unknown"}
    days = (d - today).days
    status = "overdue" if days < 0 else "soon" if days <= soon_days else "ok"
    return {"date": iso(d), "days_left": days, "status": status}


def _in_service_date(vehicle):
    """When the clock started for items never done. Imports are registered here years
    after they were built, and their fluids have been ageing since the factory."""
    d = parse_date(vehicle.get("first_registered"))
    year = int(vehicle["year"]) if vehicle.get("year") else None
    if d and (not year or year >= d.year):
        return d
    if year:
        return date(year, 1, 1)
    return None


def evaluate(vehicle, items, records, mot_tests, mileage_log, today=None):
    today = today or date.today()

    readings = [(parse_date(r["date"]), r["miles"]) for r in mileage_log]
    readings += [(parse_date(r["date"]), r["miles"]) for r in records if r.get("miles")]
    readings += [(parse_date(t["completed_date"]), t["odometer"]) for t in mot_tests if t.get("odometer")]
    model = MileageModel(readings, vehicle.get("annual_miles"), today)

    months = vehicle.get("service_interval_months") or 12
    miles = vehicle.get("service_interval_miles") or 5000

    services = [r for r in records if r.get("counts_as_service")]
    last_service = max(services, key=lambda r: (r["date"], r["id"])) if services else None

    if last_service:
        base_miles = last_service.get("miles")
        if base_miles is None:
            base_miles = model.at(parse_date(last_service["date"]))
        service = interval_due(parse_date(last_service["date"]), base_miles, months, miles, model, today)
        service["last"] = {"date": last_service["date"], "miles": last_service.get("miles"),
                           "garage": last_service.get("garage")}
    else:
        service = {"status": "unknown", "last": None, "projected_date": None, "days_left": None,
                   "due_date": None, "due_miles": None, "miles_left": None, "trigger": None}

    # when is the service after next? anything that would go overdue before then is done now
    next_date = max(parse_date(service["projected_date"]) or today, today)
    next_miles = model.at(next_date)
    following_date = add_months(next_date, months)
    if not model.stale:
        following_date = min(following_date, next_date + timedelta(days=round(miles / model.rate)))
    following_miles = next_miles + miles if next_miles is not None else None

    evaluated = []
    for item in items:
        evaluated.append(_evaluate_item(item, vehicle, records, services, model, today,
                                        next_date, following_date, following_miles))

    tax = date_status(vehicle.get("tax_due"), today)
    mot = date_status(vehicle.get("mot_due"), today)
    if (vehicle.get("tax_status") or "").upper() == "SORN":
        tax["status"] = "sorn"
        if mot["status"] in ("overdue", "unknown"):
            mot["status"] = "sorn"
    elif (vehicle.get("tax_status") or "").lower() == "untaxed":
        tax["status"] = "overdue"

    latest_mot = mot_tests[0] if mot_tests else None
    advisories = []
    if latest_mot:
        advisories = [d for d in latest_mot["defects"]
                      if (d.get("type") or "").upper() in ("ADVISORY", "MINOR", "MAJOR", "DANGEROUS", "FAIL", "PRS")]

    return {
        "mileage": model.summary(),
        "mot": mot,
        "tax": tax,
        "insurance": date_status(vehicle.get("insurance_due"), today),
        "service": service,
        "next_service_items": [e for e in evaluated if e["at_next_service"]],
        "items": evaluated,
        "latest_mot_advisories": advisories,
        "latest_mot_date": latest_mot["completed_date"] if latest_mot else None,
    }


def _evaluate_item(item, vehicle, records, services, model, today,
                   next_date, following_date, following_miles):
    done = [r for r in records if item["id"] in r.get("item_ids", [])]
    if item.get("every_service"):
        done = done + services
    if done:
        last = max(done, key=lambda r: (r["date"], r["id"]))
        base_date, base_miles, basis = parse_date(last["date"]), last.get("miles"), "record"
        if base_miles is None:
            base_miles = model.at(base_date)
    elif item.get("baseline_date") or item.get("baseline_miles") is not None:
        base_date = parse_date(item.get("baseline_date"))
        base_miles = item.get("baseline_miles")
        basis = "baseline"
    else:
        base_date, base_miles, basis = _in_service_date(vehicle), 0, "from_new"

    due = interval_due(base_date, base_miles, item.get("interval_months"),
                       item.get("interval_miles"), model, today)

    if item.get("every_service"):
        at_next, when = True, "every_service"
    elif not due:
        at_next, when = False, None
    else:
        proj = parse_date(due["projected_date"])
        if due["status"] == "overdue" or proj <= next_date + timedelta(days=SOON_DAYS):
            at_next, when = True, "due"
        elif proj <= following_date or (due["due_miles"] is not None and following_miles is not None
                                        and due["due_miles"] <= following_miles):
            at_next, when = True, "early"
        else:
            at_next, when = False, None

    return {
        **item,
        "basis": basis,
        "last_done": {"date": iso(base_date), "miles": base_miles} if basis != "from_new" else None,
        "due": due,
        "at_next_service": at_next,
        "reason": when,
    }


def upcoming(vehicle, ev, horizon_days=120):
    """Flat list of dated events for the dashboard and calendar."""
    label = vehicle.get("nickname") or f"{vehicle.get('make') or ''} {vehicle.get('model') or ''}".strip() or vehicle["reg"]
    events = []
    for kind, title in (("mot", "MOT"), ("tax", "Road tax"), ("insurance", "Insurance renewal")):
        s = ev[kind]
        if s["date"] and s["status"] != "sorn" and s["days_left"] <= horizon_days:
            events.append({"vehicle_id": vehicle["id"], "reg": vehicle["reg"], "vehicle": label,
                           "kind": kind, "title": title, "date": s["date"],
                           "days_left": s["days_left"], "status": s["status"]})
    s = ev["service"]
    if s.get("projected_date") and s["days_left"] <= horizon_days:
        extra = f" (or at {s['due_miles']:,} mi)" if s.get("due_miles") else ""
        events.append({"vehicle_id": vehicle["id"], "reg": vehicle["reg"], "vehicle": label,
                       "kind": "service", "title": "Service" + extra, "date": s["projected_date"],
                       "days_left": s["days_left"], "status": s["status"], "trigger": s.get("trigger")})
    return events
