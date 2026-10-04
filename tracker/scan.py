"""Reads photos of invoices and MOT certificates.

Text comes from macOS's built-in Vision OCR (tools/ocr); the parsing here is
rule-based, so its output is a suggestion the owner checks before saving.
"""

import os
import re
import subprocess
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_BIN = os.path.join(ROOT, "tools", "ocr")
OCR_SRC = os.path.join(ROOT, "tools", "ocr.swift")

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# each maintenance concept, as it's written on invoices and in schedule item names
CONCEPTS = {
    "oil_filter": r"oil filter|oil element",
    "oil": r"\boil\b",
    "air_filter": r"air filter|air element|air cleaner",
    "cabin_filter": r"pollen|cabin filter|micro ?filter|a/c filter|dust filter",
    "fuel_filter": r"fuel filter",
    "brake_fluid": r"brake (?:& clutch |and clutch )?fluid|brake oil",
    "coolant": r"coolant|antifreeze|anti-freeze",
    "glow_plugs": r"glow plug",
    "spark_plugs": r"spark plug|\bplugs\b",
    "cambelt": r"cam ?belt|timing belt|timing chain",
    "water_pump": r"water pump",
    "aux_belt": r"aux(?:iliary)? (?:drive )?belt|drive belt|fan belt|alternator (?:v-)?belt|v-belt|serpentine",
    "cvt": r"\bcvt\b",
    "gearbox": r"gearbox|transmission|\batf\b|diff(?:erential)?",
    "battery": r"battery",
    "wipers": r"wiper",
    "aircon": r"air[- ]?con|a/c (?:re-?)?gas|re-?gas|air conditioning",
    "tyres": r"\btyres?\b|\btires?\b",
    "chain": r"drive chain|chain (?:and|&) sprocket|chain lube|chain adjust",
    "valves": r"valve clearance|valve adjust|shim",
    "ims": r"\bims\b|intermediate shaft",
    "forks": r"fork oil|fork seal",
    "steering_head": r"steering head",
    "dpf": r"\bdpf\b|regen",
    "inspection": r"\bservice\b|inspection|health check",
}
ADVICE = re.compile(r"^\s*(advis|recommend|note[sd]?\b|monitor|suggest|customer advised|we advise)", re.I)
UK_REG = re.compile(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]\d{1,3}\s?[A-Z]{3}|[A-Z]{3}\s?\d{1,3}[A-Z]|[A-Z]{1,3}\s?\d{1,4})\b")


def ensure_ocr():
    """Compile the OCR helper on first use (needs Xcode command line tools)."""
    if os.path.exists(OCR_BIN) and os.path.getmtime(OCR_BIN) >= os.path.getmtime(OCR_SRC):
        return
    subprocess.run(["swiftc", "-O", OCR_SRC, "-o", OCR_BIN], check=True, capture_output=True, timeout=300)


def ocr(path):
    ensure_ocr()
    if path.lower().endswith(".pdf"):
        png = path + ".page1.png"
        subprocess.run(["sips", "-s", "format", "png", path, "--out", png], check=True, capture_output=True, timeout=60)
        path = png
    out = subprocess.run([OCR_BIN, path], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "Couldn't read the image")
    return out.stdout


# field extraction

def _dates(line):
    found = []
    for m in re.finditer(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", line):  # UK day first
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        found.append((y + 2000 if y < 100 else y, mo, d))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", line):
        found.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", line):
        mo = MONTHS.get(m.group(2)[:3].lower())
        if mo:
            found.append((int(m.group(3)), mo, int(m.group(1))))
    out = []
    for y, mo, d in found:
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if 1980 <= dt.year <= date.today().year + 2:
            out.append(dt)
    return out


def _date_near(lines, keywords):
    for i, line in enumerate(lines):
        if re.search(keywords, line, re.I):
            for cand in (line, lines[i + 1] if i + 1 < len(lines) else ""):
                ds = _dates(cand)
                if ds:
                    return ds[0]
    return None


def _number_near(lines, keywords):
    for i, line in enumerate(lines):
        if re.search(keywords, line, re.I):
            for cand in (line, lines[i + 1] if i + 1 < len(lines) else ""):
                tail = re.split(keywords, cand, flags=re.I)[-1] if re.search(keywords, cand, re.I) else cand
                m = re.search(r"(\d{1,3}(?:[,.\s]\d{3})+|\d{2,7})", tail)
                if m:
                    n = int(re.sub(r"\D", "", m.group(1)))
                    unit = "km" if re.search(r"\bkm\b|kilomet", cand, re.I) else "mi"
                    return n, unit
    return None, None


def _money(line):
    return [float(x.replace(",", "")) for x in re.findall(r"£\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)", line)]


def _total(lines):
    for line in reversed(lines):
        if re.search(r"\btotal\b", line, re.I) and not re.search(r"sub\s?-?total", line, re.I):
            m = _money(line) or [float(x) for x in re.findall(r"\b\d+\.\d{2}\b", line)]
            if m:
                return m[-1]
    amounts = [a for line in lines for a in _money(line)]
    return max(amounts) if amounts else None


def _garage(lines):
    for line in lines[:4]:
        t = line.strip()
        if len(re.findall(r"[A-Za-z]{2,}", t)) >= 2 and not re.search(r"invoice|receipt|date|tel|phone|www|@", t, re.I):
            return t.title() if t.isupper() else t
    return None


def _primary_concept(name):
    """The concept a schedule item is really about: the earliest (then longest) match in its name."""
    best = None
    for key, rx in CONCEPTS.items():
        m = re.search(rx, name, re.I)
        if m and (best is None or (m.start(), -(m.end() - m.start())) < best[0]):
            best = ((m.start(), -(m.end() - m.start())), key)
    return best[1] if best else None


def _regs(text):
    regs = set()
    for m in UK_REG.finditer(text.upper()):
        r = m.group(1).replace(" ", "")
        if re.search(r"\d", r) and re.search(r"[A-Z]", r) and 4 <= len(r) <= 7:
            regs.add(r)
    return regs


def parse(text, vehicle, items, all_vehicles):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    low = text.lower()
    km_car = "KILOMETRES" in (vehicle.get("notes") or "").upper()

    # which of your vehicles is this about?
    regs = _regs(text)
    known = {v["reg"]: v for v in all_vehicles}
    mentioned = [known[r] for r in regs if r in known]
    warnings = []
    if mentioned and vehicle["reg"] not in [v["reg"] for v in mentioned]:
        other = mentioned[0]
        name = other.get("nickname") or f"{other.get('make')} {other.get('model')}"
        warnings.append(f"This looks like it's for {name} ({other['reg']}), not this vehicle.")

    is_mot = bool(re.search(r"mot (?:test )?certificate|vt20|vt30|refusal of an mot|mot test result|\bmot\b.*\b(pass|fail)", low))
    if is_mot:
        miles_n, unit = _number_near(lines, r"odometer|mileage")
        adv, grab = [], False
        for line in lines:
            if re.search(r"advisor|monitor and repair", line, re.I):
                grab = True
                rest = re.split(r"advisor(?:y|ies)?:?|monitor and repair if necessary[^:]*:?", line, flags=re.I)[-1].strip(" :-")
                if len(rest) > 6:
                    adv.append(rest)
                continue
            if grab:
                if re.search(r"^(repair immediately|major|dangerous|minor|location|issued|signature|test (?:station|location|centre)|inspector|odometer|expiry)", line, re.I):
                    grab = False
                elif len(line) > 6:
                    adv.append(line)
        result = "FAILED" if re.search(r"refusal|\bfail", low) else "PASSED"
        return {
            "kind": "mot",
            "test_date": _iso(_date_near(lines, r"date of test|test date|tested on|date:")),
            "expiry_date": _iso(_date_near(lines, r"expiry date|expires|valid until")),
            "result": result,
            "odometer": _to_miles(miles_n, unit, km_car),
            "odometer_raw": f"{miles_n:,} {unit}" if miles_n else None,
            "advisories": adv[:20],
            "warnings": warnings,
        }

    # invoice / receipt
    work_lines = [l for l in lines if not ADVICE.search(l)]
    advice_lines = [l for l in lines if ADVICE.search(l)]
    work = "\n".join(work_lines).lower()
    found = {k for k, rx in CONCEPTS.items() if re.search(rx, work, re.I)}
    matched = []
    for item in items:
        c = _primary_concept(item["name"])
        if c and c in found:
            matched.append(item["id"])
    kind = ("major" if re.search(r"major service", work) else "full" if re.search(r"full service", work)
            else "interim" if re.search(r"interim service", work) else "service" if "inspection" in found
            else "tyres" if found == {"tyres"} else "repair")
    miles_n, unit = _number_near(lines, r"mileage|odometer|\bodo\b|\bmiles\b|\bmls\b|\bkm\b")
    if unit == "km" and not km_car:
        warnings.append("The mileage looked like kilometres, so it's been converted to miles. Please check it.")
    when = _date_near(lines, r"invoice date|date:|\bdate\b|dated") or (_dates(text) or [None])[0]
    if when and when > date.today():
        warnings.append("The date read as a future date, so please check it.")
    described = [l for l in work_lines if any(re.search(rx, l, re.I) for rx in CONCEPTS.values())
                 and not re.search(r"\btotal\b|vat|invoice", l, re.I)]
    notes = "; ".join(re.sub(r"\s*£\s?[\d,.]+", "", l).strip() for l in described[:12])
    if advice_lines:
        notes += ("\n" if notes else "") + "Advised: " + "; ".join(re.sub(r"^\s*advised?:?\s*", "", l, flags=re.I) for l in advice_lines[:6])
    return {
        "kind": "invoice",
        "date": _iso(when),
        "miles": _to_miles(miles_n, unit, km_car),
        "miles_raw": f"{miles_n:,} {unit}" if miles_n else None,
        "garage": _garage(lines),
        "cost": _total(lines),
        "service_kind": kind,
        "counts_as_service": kind in ("service", "full", "interim", "major"),
        "item_ids": matched,
        "notes": notes.strip(),
        "warnings": warnings,
    }


def _iso(d):
    return d.isoformat() if d else None


def _to_miles(n, unit, km_car):
    if n is None:
        return None
    return round(n * 0.621371) if unit == "km" or km_car else n
