"""DVLA Vehicle Enquiry Service + DVSA MOT History API clients.

Both return data normalised into one shape (see `lookup`). In demo mode the
responses are generated locally so you can try the app before keys arrive;
demo data only ever goes into the separate demo database.
"""

import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

DVLA_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"
MOT_URL = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/{reg}"
DEFAULT_MOT_TOKEN_URL = "https://login.microsoftonline.com/a455b827-244f-4c97-b5b4-ce5d13b4d00c/oauth2/v2.0/token"
DEFAULT_MOT_SCOPE = "https://tut.api.gov.uk/.default"

DEMO = False


class GovApiError(Exception):
    pass


def normalise_reg(reg):
    return re.sub(r"[^A-Z0-9]", "", (reg or "").upper())


def status():
    return {
        "demo": DEMO,
        "dvla": DEMO or bool(os.environ.get("DVLA_API_KEY")),
        "mot": DEMO or all(os.environ.get(k) for k in ("MOT_CLIENT_ID", "MOT_CLIENT_SECRET", "MOT_API_KEY")),
    }


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 404:
            raise GovApiError("Vehicle not found") from e
        raise GovApiError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise GovApiError(f"Network error: {e.reason}") from e


def _dvla(reg):
    key = os.environ.get("DVLA_API_KEY")
    if not key:
        raise GovApiError("DVLA_API_KEY not set")
    return _request(DVLA_URL, data=json.dumps({"registrationNumber": reg}).encode(),
                    headers={"x-api-key": key, "Content-Type": "application/json"}, method="POST")


_token = {"value": None, "expires": 0}


def _mot_token():
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["MOT_CLIENT_ID"],
        "client_secret": os.environ["MOT_CLIENT_SECRET"],
        "scope": os.environ.get("MOT_SCOPE") or DEFAULT_MOT_SCOPE,
    }).encode()
    data = _request(os.environ.get("MOT_TOKEN_URL") or DEFAULT_MOT_TOKEN_URL, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def _mot(reg):
    if not status()["mot"]:
        raise GovApiError("MOT_CLIENT_ID / MOT_CLIENT_SECRET / MOT_API_KEY not set")
    return _request(MOT_URL.format(reg=reg), headers={
        "Authorization": f"Bearer {_mot_token()}",
        "X-API-Key": os.environ["MOT_API_KEY"],
        "Accept": "application/json",
    })


def _d(value):
    """Dates arrive as '2023-10-09', '2023.10.09' or '2023-10-09T12:34:56.000Z'."""
    if not value:
        return None
    return str(value).replace(".", "-")[:10]


def _normalise_mot_tests(raw):
    tests = []
    for t in raw.get("motTests") or []:
        odo = t.get("odometerValue")
        try:
            odo = int(odo)
            if (t.get("odometerUnit") or "").upper() in ("KM", "K"):
                odo = round(odo * 0.621371)
        except (TypeError, ValueError):
            odo = None
        tests.append({
            "test_number": t.get("motTestNumber"),
            "completed_date": _d(t.get("completedDate")),
            "result": (t.get("testResult") or "").upper(),
            "expiry_date": _d(t.get("expiryDate")),
            "odometer": odo,
            "defects": [{"text": d.get("text"), "type": (d.get("type") or "").upper(),
                         "dangerous": bool(d.get("dangerous"))}
                        for d in (t.get("defects") or t.get("rfrAndComments") or [])],
        })
    tests.sort(key=lambda t: t["completed_date"] or "", reverse=True)
    return tests


def lookup(reg):
    reg = normalise_reg(reg)
    if not reg:
        raise GovApiError("Enter a registration")
    if DEMO:
        dvla, mot = _demo(reg)
        sources = {"dvla": "demo", "mot": "demo"}
    else:
        sources = {}
        dvla = mot = None
        configured = status()
        for name, fn in (("dvla", _dvla), ("mot", _mot)):
            if not configured[name]:
                sources[name] = "off"
                continue
            try:
                result = fn(reg)
                sources[name] = "live"
                if name == "dvla":
                    dvla = result
                else:
                    mot = result
            except GovApiError as e:
                sources[name] = str(e)
        if dvla is None and mot is None:
            if all(s == "off" for s in sources.values()):
                raise GovApiError("No DVSA MOT History API key yet. Enter MOT and tax dates by hand for now")
            raise GovApiError("; ".join(f"{k.upper()}: {v}" for k, v in sources.items() if v != "off"))

    dvla = dvla or {}
    mot = mot or {}
    tests = _normalise_mot_tests(mot)
    passed = [t for t in tests if t["result"] == "PASSED" and t["expiry_date"]]
    mot_candidates = [d for d in (dvla.get("motExpiryDate"), _d(mot.get("motTestDueDate")),
                                  passed[0]["expiry_date"] if passed else None) if d]
    first_reg = _d(mot.get("registrationDate")) or _d(mot.get("firstUsedDate"))
    if not first_reg and dvla.get("monthOfFirstRegistration"):
        first_reg = dvla["monthOfFirstRegistration"] + "-01"
    year = dvla.get("yearOfManufacture")
    if not year and _d(mot.get("manufactureDate")):
        year = int(_d(mot["manufactureDate"])[:4])
    engine = dvla.get("engineCapacity") or mot.get("engineSize")

    recall = mot.get("hasOutstandingRecall")
    return {
        "reg": reg,
        "make": (dvla.get("make") or mot.get("make") or "").title() or None,
        "model": (mot.get("model") or "").title() or None,
        "colour": (dvla.get("colour") or mot.get("primaryColour") or "").title() or None,
        "fuel": (dvla.get("fuelType") or mot.get("fuelType") or "").title() or None,
        "engine_cc": int(engine) if engine and str(engine).isdigit() else None,
        "year": year,
        "first_registered": first_reg,
        "tax_status": dvla.get("taxStatus"),
        "tax_due": dvla.get("taxDueDate"),
        "mot_status": dvla.get("motStatus"),
        "mot_due": max(mot_candidates) if mot_candidates else None,
        "recall_outstanding": recall if isinstance(recall, str) else ("Yes" if recall else None),
        "mot_tests": tests,
        "sources": sources,
    }


# demo data

_DEMO_CARS = [
    ("FORD", "Fiesta", "PETROL", 998), ("VOLKSWAGEN", "Golf", "DIESEL", 1968),
    ("TOYOTA", "Yaris", "HYBRID ELECTRIC", 1490), ("BMW", "3 Series", "DIESEL", 1995),
    ("LAND ROVER", "Defender", "DIESEL", 2198), ("TESLA", "Model 3", "ELECTRICITY", 0),
]
_DEMO_ADVISORIES = [
    "Nearside Front Tyre worn close to legal limit/worn on edge (5.2.3 (e))",
    "Offside Rear Brake disc worn, pitted or scored, but not seriously weakened (1.1.14 (a) (ii))",
    "Front Anti-roll bar linkage ball joint has slight play (5.3.4 (a) (i))",
    "Exhaust has a minor leak of exhaust gases (6.1.2 (a))",
    "Oil leak, but not excessive (8.4.1 (a) (i))",
]


def _demo(reg):
    rnd = random.Random(int(hashlib.md5(reg.encode()).hexdigest(), 16))
    make, model, fuel, cc = rnd.choice(_DEMO_CARS)
    today = date.today()
    first = today - timedelta(days=rnd.randint(4 * 365, 11 * 365))
    per_year = rnd.randint(5000, 11000)
    mot_due = today + timedelta(days=rnd.randint(-10, 300))
    tests, when, odo = [], mot_due - timedelta(days=365), 0
    first_mot = first.replace(year=first.year + 3)
    while when >= first_mot:
        years = (when - first).days / 365.25
        odo = int(per_year * years * rnd.uniform(0.95, 1.05))
        defects = [{"text": t, "type": "ADVISORY", "dangerous": False}
                   for t in rnd.sample(_DEMO_ADVISORIES, rnd.randint(0, 2))]
        tests.append({"motTestNumber": str(rnd.randint(10**11, 10**12)),
                      "completedDate": when.isoformat() + "T10:00:00.000Z",
                      "testResult": "PASSED", "expiryDate": (when + timedelta(days=365)).isoformat(),
                      "odometerValue": str(odo), "odometerUnit": "MI", "defects": defects})
        when -= timedelta(days=365)
    tax_due = today + timedelta(days=rnd.randint(5, 330))
    dvla = {"registrationNumber": reg, "make": make, "colour": rnd.choice(["BLUE", "GREY", "WHITE", "BLACK"]),
            "fuelType": fuel, "engineCapacity": cc or None, "yearOfManufacture": first.year,
            "monthOfFirstRegistration": first.strftime("%Y-%m"), "taxStatus": "Taxed",
            "taxDueDate": tax_due.replace(day=1).isoformat(), "motStatus": "Valid",
            "motExpiryDate": mot_due.isoformat()}
    mot = {"registration": reg, "make": make, "model": model.upper(), "fuelType": fuel,
           "registrationDate": first.isoformat(), "motTests": tests,
           "hasOutstandingRecall": "No"}
    return dvla, mot
