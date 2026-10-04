"use strict";

// helpers

const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");
const dialog = $("#dialog");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const today = () => new Date().toISOString().slice(0, 10);
const miles = (n) => (n == null ? "–" : `${Math.round(n).toLocaleString("en-GB")} mi`);

function fmtDate(d) {
  if (!d) return "–";
  return new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function rel(days) {
  if (days == null) return "";
  if (days === 0) return "today";
  if (days < 0) return `${abs(days)} overdue`;
  return `in ${abs(days)}`;
}
function abs(days) {
  const d = Math.abs(days);
  if (d < 45) return `${d} day${d === 1 ? "" : "s"}`;
  if (d < 730) return `${Math.round(d / 30.4)} months`;
  return `${(d / 365.25).toFixed(1)} years`;
}

const STATUS_TEXT = { ok: "OK", soon: "Due soon", overdue: "Overdue", unknown: "Not set", sorn: "SORN" };
const pill = (status, text) =>
  `<span class="pill s-${status}">${esc(text ?? STATUS_TEXT[status] ?? status)}</span>`;
const rinSvg = (cls = "rin") => `<svg class="${cls}" viewBox="0 0 240 300" aria-hidden="true"><use href="#rin"/></svg>`;

// Writes made while the Mac is unreachable wait here and are replayed in order once it's back.
// Adding vehicles, DVLA/DVSA refreshes, templates and chat need the Mac, so they aren't queued.
const QUEUE_KEY = "garage-pending-v1";
const NOT_QUEUEABLE = [/^\/api\/vehicles$/, /\/sync$/, /\/template$/, /^\/api\/chat/];
const net = { offline: false, since: null };

function loadQueue() {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); } catch { return []; }
}
function saveQueue(q) {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); } catch {}
  updateSyncBadge();
}

async function send(method, path, body) {
  return fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function api(method, path, body) {
  let res;
  try {
    res = await send(method, path, body);
  } catch (err) {
    if (method === "GET") throw new Error("Can't reach the garage.");
    if (NOT_QUEUEABLE.some((re) => re.test(path))) throw new Error("That needs the Mac to be on. Try again when it's reachable.");
    saveQueue([...loadQueue(), { method, path, body, at: new Date().toISOString() }]);
    setOffline(true);
    toast("Saved on this phone. It'll sync when the Mac is reachable.");
    return { queued: true };
  }
  if (method === "GET") setOffline(!!res.headers.get("X-Offline"), res.headers.get("Date"));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function flushQueue() {
  let q = loadQueue();
  if (!q.length) return;
  let sent = 0;
  while (q.length) {
    const job = q[0];
    let res;
    try { res = await send(job.method, job.path, job.body); } catch { break; }  // still unreachable
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      toast(`Couldn't sync a change from ${fmtDate(job.at.slice(0, 10))}: ${data.error || res.status}`);
    }
    q = q.slice(1);
    saveQueue(q);
    sent++;
  }
  if (sent) { toast(`Synced ${sent} change${sent === 1 ? "" : "s"} from this phone.`); render(); }
}

function setOffline(offline, dateHeader) {
  net.offline = offline;
  net.since = offline && dateHeader ? new Date(dateHeader) : null;
  updateSyncBadge();
}

function updateSyncBadge() {
  const b = document.getElementById("sync-badge");
  if (!b) return;
  const pending = loadQueue().length;
  const since = net.since ? net.since.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : null;
  const parts = [];
  if (net.offline) parts.push(since ? `Offline · data from ${since}` : "Offline");
  if (pending) parts.push(`${pending} change${pending === 1 ? "" : "s"} waiting to sync`);
  b.hidden = !parts.length;
  b.textContent = parts.join(" · ");
}

// keep every vehicle page saved on the phone, not just the ones you happened to open
function prefetchVehicles(vehicles) {
  if (net.offline || !navigator.serviceWorker?.controller) return;
  for (const v of vehicles) fetch(`/api/vehicles/${v.id}`).catch(() => {});
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3500);
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") {
      if (el.name.endsWith("[]")) {
        const key = el.name.slice(0, -2);
        out[key] = out[key] || [];
        if (el.checked) out[key].push(Number(el.value));
      } else out[el.name] = el.checked;
    } else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
    else out[el.name] = el.value.trim();
  }
  return out;
}

function openDialog(html, onSubmit) {
  dialog.innerHTML = `<form method="dialog">${html}</form>`;
  const form = $("form", dialog);
  form.addEventListener("submit", async (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      await onSubmit(formData(form), form);
      dialog.close();
    } catch (err) {
      toast(err.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  });
  dialog.showModal();
  return form;
}

const actions = (label = "Save") =>
  `<div class="actions"><button value="cancel" formnovalidate>Cancel</button><button class="primary" type="submit">${label}</button></div>`;

// router

let state = { dashboard: null, vehicle: null, tab: "next" };

async function render() {
  const m = location.hash.match(/^#\/v\/(\d+)/);
  try {
    state.dashboard = await api("GET", "/api/dashboard");
    setChatMode(state.dashboard.assistant?.ok);
    prefetchVehicles(state.dashboard.vehicles);
    $("#demo-badge").hidden = !state.dashboard.apis.demo;
    if (m) {
      state.vehicle = await api("GET", `/api/vehicles/${m[1]}`);
      renderVehicle(state.vehicle);
    } else {
      renderDashboard(state.dashboard);
    }
  } catch (err) {
    app.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}
window.addEventListener("hashchange", () => { state.tab = "next"; render(); window.scrollTo(0, 0); });

// dashboard

function renderDashboard(d) {
  const apiNote = !d.apis.mot
    ? `<div class="banner">The DVSA MOT History API key isn't set yet, so make, model, MOT dates and history won't fill in automatically. Add it to <code>.env</code> (see README), or run with <code>--demo</code> to try sample data.</div>`
    : "";

  if (!d.vehicles.length) {
    app.innerHTML = `<div class="stack">${apiNote}
      <div class="panel empty">${rinSvg()}<h2>No vehicles yet</h2><p>Add a vehicle by registration to track its MOT, tax, insurance and servicing.</p>
      <button class="primary" onclick="addVehicle()">+ Add vehicle</button></div></div>`;
    return;
  }

  const upcoming = d.upcoming.length
    ? `<ul class="list">${d.upcoming.map((e) => `
        <li><span class="plate">${esc(e.reg)}</span>
          <div class="grow"><a href="#/v/${e.vehicle_id}"><b>${esc(e.title)}</b></a> <span class="muted">· ${esc(e.vehicle)}</span>
          ${e.trigger === "mileage" ? `<span class="muted small"> · by mileage (est.)</span>` : ""}</div>
          <span class="muted small num">${fmtDate(e.date)}</span>
          ${pill(e.status, rel(e.days_left))}</li>`).join("")}</ul>`
    : `<p class="muted">Nothing due in the next 4 months.</p>`;

  app.innerHTML = `<div class="stack">
    ${hero(d)}
    ${apiNote}
    <section class="panel"><div class="head"><h2>Coming up</h2><span class="muted small">next 4 months, plus anything overdue</span></div>
      <div class="body">${upcoming}</div></section>
    <section class="grid cards">${d.vehicles.map(vehicleCard).join("")}</section>
  </div>`;
}

// Rin's briefing, laid out as a streetwear poster
function hero(d) {
  const over = d.upcoming.filter((e) => e.status === "overdue");
  const soon = d.upcoming.filter((e) => e.status === "soon");
  const job = (e) => `<b>${esc(e.vehicle)}</b> ${esc(e.title.replace(/ \(or at .*\)/, "").toLowerCase())}`;
  let line, stamp, mood;
  if (over.length) {
    line = `${over.length} job${over.length === 1 ? " is" : "s are"} overdue: ${over.slice(0, 3).map(job).join(", ")}${over.length > 3 ? " and more" : ""}. Let's get them sorted.`;
    stamp = "OVERDUE"; mood = "overdue";
  } else if (soon.length) {
    line = `Heads up, ${job(soon[0])} is due ${esc(rel(soon[0].days_left))}.`;
    stamp = "DUE SOON"; mood = "soon";
  } else {
    line = "Garage is all clear. Nothing due for the next 4 months.";
    stamp = "ALL CLEAR"; mood = "clear";
  }
  const n = String(d.vehicles.length).padStart(2, "0");
  const sorn = d.vehicles.filter((v) => v.status.tax.status === "sorn").length;
  const streaks = [[-18, 150, 120], [196, 178, 70], [-30, 206, 90], [210, 232, 46]]
    .map(([x, y, w]) => `<span class="streak" style="left:${60 + x}px;top:${26 + y}px;width:${w}px"></span>`).join("");
  return `<section class="panel poster mood-${mood}">
    <div class="p-top"><span class="red">Project ${n}</span><i class="rule"></i><span>Vehicle Tracker</span></div>
    <div class="p-grid">
      <div class="p-left">
        <div class="p-title" aria-hidden="true">整<span class="red">備</span></div>
        <div class="p-tag">Service is power</div>
        <div class="p-list">Inspect<br>Maintain<br>Drive on</div>
        <div class="bubble"><span class="who">RIN · MECHANIC</span>${line}
          <div class="small muted" style="margin-top:6px">${d.vehicles.length} vehicles in the garage${sorn ? `, ${sorn} laid up on SORN` : ""}.${d.assistant?.ok ? " Tell me what you've done and I'll log it." : " Ask me anything about them: dates, services, tyre pressures."}</div></div>
      </div>
      <div class="p-art">
        <div class="sun"></div>${streaks}
        <svg class="blossom" viewBox="0 0 190 170" aria-hidden="true"><use href="#branch"/></svg>
        <svg class="car" viewBox="0 0 360 130" aria-hidden="true"><use href="#car"/></svg>
        <svg class="rin" viewBox="0 0 240 300" aria-hidden="true"><use href="#rin"/></svg>
        <span class="stamp">${stamp}</span>
      </div>
      <div class="p-right">
        <div class="box"><div class="k">GARAGE</div><div class="n">${n}</div></div>
        <div class="crosshair"></div>
        <div class="vert" aria-hidden="true">夜間<span class="red">整備</span></div>
        <div class="barcode"></div>
      </div>
    </div>
    <div class="p-bottom"><span class="slashes"></span><span>Built different · serviced on time</span><i class="rule"></i><span class="red">Est. 2026</span></div>
  </section>`;
}

const bar = (psi) => (psi * 0.0689476).toFixed(1);
const psiTxt = (n) => (n == null ? "–" : Number.isInteger(n) ? String(n) : n.toFixed(1));

function tyreRow(v) {
  const f = v.tyre_front_psi, r = v.tyre_rear_psi;
  if (f == null && r == null) return `<div class="row tyres"><span class="muted">Tyres</span><span class="muted small">${esc(v.tyre_note || "Pressures not set")}</span><span></span></div>`;
  const laden = v.tyre_front_laden_psi != null || v.tyre_rear_laden_psi != null
    ? `<div class="muted small">Loaded: F ${psiTxt(v.tyre_front_laden_psi ?? f)} · R ${psiTxt(v.tyre_rear_laden_psi ?? r)} psi</div>` : "";
  return `<div class="row tyres"><span class="muted">Tyres</span>
    <span><b class="num">F ${psiTxt(f)} · R ${psiTxt(r)}</b> <span class="muted small">psi</span>
      <div class="muted small num">${f != null ? bar(f) : "–"} / ${r != null ? bar(r) : "–"} bar${v.tyre_size ? ` · ${esc(v.tyre_size)}` : ""}</div>${laden}
      ${/unconfirmed/i.test(v.tyre_note || "") ? `<div class="small red">Unconfirmed: check the label</div>` : ""}</span>
    <span></span></div>`;
}

function vehicleCard(v, i) {
  const s = v.status;
  const row = (k, st, date, extra = "") =>
    `<div class="row"><span class="muted">${k}</span><span class="num">${fmtDate(date)}${extra}</span>${pill(st.status, st.status === "ok" ? rel(st.days_left) : undefined)}</div>`;
  const svc = s.service;
  const svcExtra = svc.due_miles ? ` <span class="muted small">/ ${miles(svc.due_miles)}</span>` : "";
  return `<article class="panel vcard" onclick="location.hash='#/v/${v.id}'">
    <span class="bay">${String(i + 1).padStart(2, "0")}</span>
    <div class="tag">BAY ${String(i + 1).padStart(2, "0")}</div>
    <div class="title"><div><div class="name">${esc(v.label)}</div>
      <div class="muted small">${esc([v.year, v.fuel, v.colour].filter(Boolean).join(" · "))}</div></div>
      <span class="plate">${esc(v.reg)}</span></div>
    <div class="muted small">${s.mileage.stale
      ? (s.mileage.last_reading ? `Last reading ${miles(s.mileage.last_reading.miles)} (${fmtDate(s.mileage.last_reading.date)}). Add a current one.` : "No mileage readings yet")
      : `≈ ${miles(s.mileage.estimated_now)}`}</div>
    <div class="rows">
      ${row("MOT", s.mot, s.mot.date)}
      ${row("Tax", s.tax, s.tax.date)}
      ${row("Insurance", s.insurance, s.insurance.date)}
      ${row("Service", svc, svc.projected_date, svcExtra)}
      ${tyreRow(v)}
    </div>
  </article>`;
}

// vehicle page

function renderVehicle(v) {
  const ev = v.eval;
  const svc = ev.service;
  const tile = (k, st, value, sub) => `<div class="panel tile"><div class="k">${k}</div>
    <div class="v num">${value}</div><div class="sub">${pill(st.status, st.status === "ok" ? rel(st.days_left) : (st.days_left != null ? rel(st.days_left) : undefined))} ${sub || ""}</div></div>`;

  const svcSub = svc.status === "unknown"
    ? "Log your last service to start the clock."
    : `${svc.trigger === "mileage" ? "Mileage limit first" : "12-month limit first"} · at ${miles(svc.due_miles)}`;

  const tabs = [["next", "Next service"], ["schedule", "Maintenance schedule"], ["history", "Work history"], ["mot", "MOT history"], ["mileage", "Mileage"], ["docs", "Documents"], ["details", "Details"]];

  app.innerHTML = `<a class="back" href="#/">← All vehicles</a>
  <div class="stack">
    <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      <span class="plate" style="font-size:18px">${esc(v.reg)}</span>
      <div style="flex:1;min-width:200px"><h1>${esc(v.label)}</h1>
        <div class="muted small">${esc([v.make, v.model, v.variant, v.year, v.fuel, v.engine_cc ? v.engine_cc + "cc" : null, v.colour].filter(Boolean).join(" · "))}</div></div>
      <button onclick="syncVehicle(${v.id})" title="${esc(v.sync_sources || "")}">↻ Refresh DVLA/DVSA</button>
      <button onclick="$('#scan-input').click()" title="Photo or PDF of an invoice or MOT certificate">📷 Scan document</button>
      <button class="primary" onclick="logWork()">Log work</button>
    </div>
    ${v.recall_outstanding && v.recall_outstanding !== "No" && v.recall_outstanding !== "Unknown" ? `<div class="banner">DVSA reports an outstanding safety recall (${esc(v.recall_outstanding)}). Contact a dealer.</div>` : ""}
    <div class="grid tiles">
      ${tile("MOT", ev.mot, fmtDate(ev.mot.date), esc(v.mot_status || ""))}
      ${tile("Road tax", ev.tax, ev.tax.status === "sorn" ? "SORN" : fmtDate(ev.tax.date),
        state.dashboard.apis.dvla ? esc(v.tax_status || "") : `<a href="https://vehicleenquiry.service.gov.uk/" target="_blank" rel="noopener">Check on GOV.UK</a>, then add it in Details`)}
      ${tile("Insurance", ev.insurance, fmtDate(ev.insurance.date), esc(v.insurance_provider || (ev.insurance.date ? "" : "Add in Details")))}
      ${tile("Next service", svc, fmtDate(svc.projected_date), esc(svcSub))}
    </div>
    <section class="panel">
      <nav class="tabs">${tabs.map(([k, l]) => `<button class="${state.tab === k ? "active" : ""}" onclick="state.tab='${k}';renderVehicle(state.vehicle)">${l}</button>`).join("")}</nav>
      <div class="body">${TABS[state.tab](v)}</div>
    </section>
  </div>`;
}

const REASON = { due: "Due", early: "Do early", every_service: "Every service" };

const TABS = {
  next(v) {
    const ev = v.eval;
    const items = ev.next_service_items;
    const last = ev.service.last;
    const list = items.length
      ? `<ul class="list">${items.map((i) => `<li>
          <div class="grow"><b>${esc(i.name)}</b>${i.notes ? `<div class="muted small">${esc(i.notes)}</div>` : ""}
            ${i.basis === "from_new" && i.reason !== "every_service" ? `<div class="muted small">No record of this being done, so it's assumed due from new. If it's been done, log it or set when in the schedule.</div>` : ""}</div>
          <span class="muted small num">${i.due ? `${fmtDate(i.due.due_date)}${i.due.due_miles ? " / " + miles(i.due.due_miles) : ""}` : ""}</span>
          ${pill(i.reason === "due" ? (i.due?.status === "overdue" ? "overdue" : "soon") : "ok", REASON[i.reason])}</li>`).join("")}</ul>`
      : `<p class="muted">No schedule items yet. Add some in the Maintenance schedule tab.</p>`;
    const adv = ev.latest_mot_advisories.length
      ? `<h3 style="margin-top:18px">From the last MOT (${fmtDate(ev.latest_mot_date)})</h3>
         ${ev.latest_mot_advisories.map((d) => `<div class="defect t-${esc(d.type)}"><b>${esc(d.type)}</b>${esc(d.text)}</div>`).join("")}`
      : "";
    return `<p class="muted small" style="margin-top:0">Serviced every ${v.service_interval_months} months or ${miles(v.service_interval_miles)}, whichever comes first.
      ${last ? `Last service ${fmtDate(last.date)}${last.miles ? " at " + miles(last.miles) : ""}${last.garage ? " (" + esc(last.garage) + ")" : ""}.` : "<b>No service logged yet.</b>"}
      ${ev.mileage.stale
        ? `<b>No recent odometer reading</b>, so only the time limits are being used. Add a current reading in the Mileage tab.`
        : `Using about ${miles(ev.mileage.annual_miles)} a year (${{ history: "from your readings", set: "as you set it", default: "UK average, until there are readings" }[ev.mileage.basis]}).`}</p>
      ${list}${adv}`;
  },

  schedule(v) {
    const rows = v.eval.items.map((i) => `<tr>
      <td><b>${esc(i.name)}</b>${i.notes ? `<div class="muted small">${esc(i.notes)}</div>` : ""}</td>
      <td class="num">${i.every_service ? "Every service" : [i.interval_months ? `${i.interval_months} mo` : null, i.interval_miles ? miles(i.interval_miles) : null].filter(Boolean).join(" / ") || "–"}</td>
      <td class="num">${i.last_done ? `${fmtDate(i.last_done.date)}${i.last_done.miles != null ? `<div class="muted small">${miles(i.last_done.miles)}</div>` : ""}` : `<span class="muted">No record</span>`}</td>
      <td class="num">${i.due ? `${fmtDate(i.due.projected_date)}${i.due.due_miles ? `<div class="muted small">or ${miles(i.due.due_miles)}</div>` : ""}` : "–"}</td>
      <td>${i.every_service ? "" : i.due ? pill(i.due.status) : ""}</td>
      <td><button class="ghost" onclick="editItem(${i.id})">Edit</button></td></tr>`).join("");
    const source = [...new Set(v.eval.items.map((i) => i.source).filter(Boolean))].join("; ");
    return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <button onclick="editItem()">+ Add item</button>
        <button onclick="applyTemplate()">Load a template…</button>
        <button onclick="openChat('Research the official manufacturer maintenance schedule for vehicle ${v.id} (${esc(v.reg)}) and propose it for my schedule.')">Research with assistant</button>
      </div>
      ${source ? `<p class="muted small">Source: ${esc(source)}</p>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Item</th><th>Interval</th><th>Last done</th><th>Next due</th><th></th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="muted">No items.</td></tr>`}</tbody></table></div>`;
  },

  docs(v) {
    if (!v.documents.length) return `<p class="muted">No documents yet. Use <b>📷 Scan document</b> to add photos of invoices and MOT certificates. Rin reads them and fills in the details for you.</p>`;
    return `<div class="docs">${v.documents.map((d) => {
      const rec = v.records.find((r) => r.id === d.record_id);
      const what = rec ? `${fmtDate(rec.date)} · ${esc(rec.kind)}` : d.mot_test_id ? "MOT certificate" : "Not linked to a record";
      return `<figure class="doc"><a href="/documents/${d.id}" target="_blank" rel="noopener"><img src="/documents/${d.id}" alt="" loading="lazy"
          onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pdf',textContent:'PDF'}))"></a>
        <figcaption><b>${d.kind === "mot" ? "MOT" : "Invoice"}</b> <span class="muted">${what}</span>
          <span class="muted small">added ${fmtDate(d.uploaded_at.slice(0, 10))}</span>
          <button class="ghost danger" onclick="deleteDocument(${d.id})">Delete</button></figcaption></figure>`;
    }).join("")}</div>`;
  },

  history(v) {
    if (!v.records.length) return `<p class="muted">No work logged yet. <a href="javascript:logWork()">Log your last service</a> so the app knows when the next one is due.</p>`;
    return `<ul class="list">${v.records.map((r) => `<li style="align-items:flex-start">
      <div style="width:110px" class="num"><b>${fmtDate(r.date)}</b><div class="muted small">${r.miles != null ? miles(r.miles) : ""}</div></div>
      <div class="grow"><b>${esc(r.kind)}</b>${r.counts_as_service ? ` <span class="pill s-ok">service</span>` : ""}
        ${r.garage ? ` <span class="muted">· ${esc(r.garage)}</span>` : ""}${r.cost != null ? ` <span class="muted">· £${Number(r.cost).toFixed(2)}</span>` : ""}
        ${r.notes ? `<div class="small">${esc(r.notes)}</div>` : ""}
        ${v.documents.filter((d) => d.record_id === r.id).map((d) => `<a class="small" href="/documents/${d.id}" target="_blank" rel="noopener">📄 View invoice</a>`).join(" ")}
        ${r.items.length ? `<div class="muted small">✓ ${r.items.map(esc).join(" · ")}</div>` : ""}</div>
      <button class="ghost danger" onclick="deleteRecord(${r.id})">Delete</button></li>`).join("")}</ul>`;
  },

  mot(v) {
    if (!v.mot_tests.length) return `<p class="muted">No MOT history. Tap “Refresh DVLA/DVSA” once your MOT History API key is set. New vehicles don't need an MOT until they're 3 years old.</p>`;
    return `<ul class="list">${v.mot_tests.map((t) => `<li style="align-items:flex-start">
      <div style="width:110px" class="num"><b>${fmtDate(t.completed_date)}</b><div class="muted small">${miles(t.odometer)}</div></div>
      <div class="grow">${pill(t.result === "PASSED" ? "ok" : "overdue", t.result === "PASSED" ? "Pass" : "Fail")}
        ${t.expiry_date ? `<span class="muted small"> expires ${fmtDate(t.expiry_date)}</span>` : ""}
        ${t.defects.map((d) => `<div class="defect t-${esc(d.type)}"><b>${esc(d.type)}</b>${esc(d.text)}</div>`).join("")}</div></li>`).join("")}</ul>`;
  },

  mileage(v) {
    const m = v.eval.mileage;
    return `<p class="muted small" style="margin-top:0">${m.stale ? "<b>No reading in the last 18 months.</b> Add the current odometer." : `Estimated now: <b>${miles(m.estimated_now)}</b> · ${miles(m.annual_miles)}/year.`}
      Service records and MOT tests count as readings too.</p>
      <form onsubmit="addMileage(event)" style="display:flex;gap:8px;max-width:420px;margin-bottom:12px">
        <input type="date" name="date" value="${today()}"><input type="number" name="miles" placeholder="Odometer" required>
        <button class="primary">Add</button></form>
      <ul class="list">${v.mileage_log.map((r) => `<li><span class="num" style="width:110px">${fmtDate(r.date)}</span>
        <span class="grow num">${miles(r.miles)}</span><button class="ghost danger" onclick="deleteMileage(${r.id})">Delete</button></li>`).join("") || `<li class="muted">No manual readings.</li>`}</ul>`;
  },

  details(v) {
    const f = (name, label, type = "text", extra = "") =>
      `<label>${label}<input type="${type}" name="${name}" value="${esc(v[name] ?? "")}" ${extra}></label>`;
    return `<form id="details-form" class="form-grid" onsubmit="saveDetails(event)">
      ${f("nickname", "Nickname")}${f("variant", "Variant / engine (e.g. 1.0 EcoBoost 125 ST-Line)")}
      ${f("make", "Make")}${f("model", "Model")}
      ${f("year", "Year", "number")}${f("fuel", "Fuel")}
      ${f("insurance_provider", "Insurer")}${f("insurance_policy", "Policy number")}
      ${f("insurance_due", "Insurance renewal", "date")}${f("annual_miles", "Annual mileage override (blank = learn from readings)", "number")}
      ${f("service_interval_months", "Service every (months)", "number", "min=1")}${f("service_interval_miles", "…or every (miles)", "number", "min=500")}
      ${f("mot_due", "MOT due (normally from DVSA)", "date")}${f("tax_due", state.dashboard.apis.dvla ? "Tax due (normally from DVLA)" : "Tax due", "date")}
      <h3 class="full" style="margin-top:8px">Tyre pressures (cold, psi)</h3>
      ${f("tyre_front_psi", "Front", "number", 'step="0.5"')}${f("tyre_rear_psi", "Rear", "number", 'step="0.5"')}
      ${f("tyre_front_laden_psi", "Front, fully loaded (optional)", "number", 'step="0.5"')}${f("tyre_rear_laden_psi", "Rear, fully loaded (optional)", "number", 'step="0.5"')}
      ${f("tyre_size", "Tyre size (e.g. 225/40 R18)")}${f("tyre_note", "Tyre note (e.g. source, track settings)")}
      <label class="full">Notes<textarea name="notes">${esc(v.notes ?? "")}</textarea></label>
      <div class="full" style="display:flex;gap:8px;justify-content:space-between">
        <button type="button" class="danger" onclick="deleteVehicle(${v.id})">Delete vehicle</button>
        <button class="primary">Save details</button></div>
      <p class="full muted small">Last synced: ${v.last_synced ? esc(v.last_synced.replace("T", " ")) : "never"} ${v.sync_sources ? "· " + esc(v.sync_sources) : ""}</p>
    </form>`;
  },
};

// actions

async function addVehicle() {
  const templates = await api("GET", "/api/templates");
  openDialog(`<h2>Add vehicle</h2>
    <div class="form-grid">
      <label class="full">Registration<input name="reg" required placeholder="AB12 CDE" style="text-transform:uppercase"></label>
      <label>Nickname (optional)<input name="nickname" placeholder="The van"></label>
      <label>Insurance renewal<input type="date" name="insurance_due"></label>
      <label class="full">Maintenance schedule<select name="template"><option value="">Pick automatically by fuel type</option>
        ${templates.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</select></label>
    </div>
    <p class="muted small">Make, model, tax and MOT dates, and the full MOT history are fetched from DVLA/DVSA.</p>
    ${actions("Add")}`, async (data) => {
    const res = await api("POST", "/api/vehicles", data);
    if (res.warnings?.length) toast(res.warnings.join(" · "));
    location.hash = `#/v/${res.id}`;
  });
}

function scanPanel(scan) {
  if (!scan) return "";
  const warn = scan.parsed.warnings.map((w) => `<div class="scan-warn">⚠ ${esc(w)}</div>`).join("");
  return `<div class="scan-box full">
    <a href="/documents/${scan.document_id}" target="_blank" rel="noopener"><img src="/documents/${scan.document_id}" alt="" onerror="this.remove()"></a>
    <div><b>Rin read this document.</b> Check everything below before saving. Invoices vary, so she can miss or mis-read things.${warn}
      <details><summary>Text she read</summary><pre>${esc(scan.text || "(nothing)")}</pre></details></div>
  </div>`;
}

function logWork(scan) {
  const v = state.vehicle;
  const items = v.eval.items;
  const p = scan?.parsed;
  const due = new Set(p ? p.item_ids : v.eval.next_service_items.map((i) => i.id));
  const kinds = [["service", "Service"], ["interim", "Interim service"], ["full", "Full service"], ["major", "Major service"],
    ["repair", "Repair"], ["tyres", "Tyres"], ["mot-work", "MOT work"], ["other", "Other"]];
  const kind = p?.service_kind || "service";
  const form = openDialog(`<h2>${p ? "Check & save" : "Log work"} · ${esc(v.label)}</h2>
    <div class="form-grid">
      ${scanPanel(scan)}
      ${scan ? `<input type="hidden" name="document_id" value="${scan.document_id}">` : ""}
      <label>Date<input type="date" name="date" value="${p?.date || today()}" required></label>
      <label>Odometer${p?.miles_raw && p.miles_raw.endsWith("km") ? ` <span class="muted">(read ${esc(p.miles_raw)})</span>` : ""}<input type="number" name="miles" value="${p?.miles ?? ""}" placeholder="${v.eval.mileage.estimated_now && !v.eval.mileage.stale ? "≈ " + v.eval.mileage.estimated_now : ""}"></label>
      <label>Type<select name="kind">${kinds.map(([k, l]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Garage<input name="garage" value="${esc(p?.garage || "")}"></label>
      <label>Cost (£)<input type="number" step="0.01" name="cost" value="${p?.cost ?? ""}"></label>
      <label class="check" style="align-self:end"><input type="checkbox" name="counts_as_service" ${!p || p.counts_as_service ? "checked" : ""}> Counts as a service (resets the 12 month / 5,000 mile clock)</label>
      <div class="full"><label>Work done</label><div class="items-box">
        ${items.map((i) => `<label class="check"><input type="checkbox" name="item_ids[]" value="${i.id}" ${due.has(i.id) ? "checked" : ""}> ${esc(i.name)}</label>`).join("") || `<span class="muted small">No schedule items</span>`}
      </div></div>
      <label class="full">Notes<textarea name="notes" placeholder="Anything else done, parts used, advice given">${esc(p?.notes || "")}</textarea></label>
    </div>${actions()}`, async (data) => {
    const res = await api("POST", `/api/vehicles/${v.id}/records`, data);
    if (!res.queued) toast("Logged");
    render();
  });
  // one-off jobs shouldn't pre-tick the whole service list
  form.kind.addEventListener("change", () => {
    const svc = ["service", "interim", "full", "major"].includes(form.kind.value);
    form.counts_as_service.checked = svc;
    for (const cb of form.querySelectorAll('[name="item_ids[]"]')) cb.checked = svc && due.has(Number(cb.value));
  });
}

function editItem(id) {
  const v = state.vehicle;
  const i = id ? v.eval.items.find((x) => x.id === id) : {};
  const f = (name, label, type = "text") => `<label>${label}<input type="${type}" name="${name}" value="${esc(i[name] ?? "")}"></label>`;
  openDialog(`<h2>${id ? "Edit" : "Add"} schedule item</h2>
    <div class="form-grid">
      <label class="full">Name<input name="name" required value="${esc(i.name ?? "")}"></label>
      ${f("interval_months", "Every (months)", "number")}${f("interval_miles", "…or every (miles)", "number")}
      <label class="check full"><input type="checkbox" name="every_service" ${i.every_service ? "checked" : ""}> Do at every service</label>
      <label class="full">Notes<textarea name="notes">${esc(i.notes ?? "")}</textarea></label>
      <p class="full muted small" style="margin:0">Last done before you started using this app? Fill in when, so the next due date is right.</p>
      ${f("baseline_date", "Last done (date)", "date")}${f("baseline_miles", "Last done (miles)", "number")}
    </div>
    <div class="actions">${id ? `<button type="button" class="danger" style="margin-right:auto" onclick="deleteItem(${id})">Delete</button>` : ""}
      <button value="cancel" formnovalidate>Cancel</button><button class="primary" type="submit">Save</button></div>`,
  async (data) => {
    if (id) await api("PATCH", `/api/items/${id}`, data);
    else await api("POST", `/api/vehicles/${v.id}/items`, { ...data, source: "Manual" });
    render();
  });
}

// photos are shrunk on the phone first: quicker to send, and still sharp enough to read
async function fileToDataUrl(file) {
  if (file.type.startsWith("image/")) {
    try {
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      return { data: c.toDataURL("image/jpeg", 0.88), name: "scan.jpg" };
    } catch { /* fall through and send the original */ }
  }
  const data = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(file); });
  return { data, name: file.name };
}

async function scanDocument(file) {
  if (!file) return;
  const v = state.vehicle;
  toast("Rin is reading it…");
  try {
    const body = await fileToDataUrl(file);
    const res = await api("POST", `/api/vehicles/${v.id}/scan`, body);
    if (res.queued) return;
    if (res.parsed.kind === "mot") motDialog(res); else logWork(res);
  } catch (err) {
    toast(err.message.includes("reach") ? "Scanning needs the Mac to be on." : err.message);
  }
}

function motDialog(scan) {
  const v = state.vehicle;
  const p = scan.parsed;
  openDialog(`<h2>Check & save MOT · ${esc(v.label)}</h2>
    <div class="form-grid">
      ${scanPanel(scan)}
      <input type="hidden" name="document_id" value="${scan.document_id}">
      <label>Test date<input type="date" name="completed_date" value="${p.test_date || ""}" required></label>
      <label>Expiry date<input type="date" name="expiry_date" value="${p.expiry_date || ""}"></label>
      <label>Result<select name="result"><option value="PASSED" ${p.result === "PASSED" ? "selected" : ""}>Pass</option><option value="FAILED" ${p.result === "FAILED" ? "selected" : ""}>Fail</option></select></label>
      <label>Odometer (miles)<input type="number" name="odometer" value="${p.odometer ?? ""}"></label>
      <label class="full">Advisories, one per line<textarea name="advisories" rows="5">${esc(p.advisories.join("\n"))}</textarea></label>
    </div>${actions()}`, async (data) => {
    data.advisories = (data.advisories || "").split("\n").map((s) => s.trim()).filter(Boolean);
    await api("POST", `/api/vehicles/${v.id}/mot-scan`, data);
    toast("MOT saved");
    render();
  });
}

async function deleteDocument(id) {
  if (!confirm("Delete this document?")) return;
  await api("DELETE", `/api/documents/${id}`);
  render();
}

async function deleteItem(id) {
  if (!confirm("Delete this schedule item?")) return;
  await api("DELETE", `/api/items/${id}`);
  dialog.close();
  render();
}

async function applyTemplate() {
  const templates = await api("GET", "/api/templates");
  openDialog(`<h2>Load a schedule template</h2>
    <label>Template<select name="template">${templates.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</select></label>
    <p class="muted small">This replaces the current schedule. Work history is kept, but its ticks for removed items are lost.</p>${actions("Replace schedule")}`,
  async (data) => {
    await api("POST", `/api/vehicles/${state.vehicle.id}/template`, data);
    render();
  });
}

async function syncVehicle(id) {
  try {
    const res = await api("POST", `/api/vehicles/${id}/sync`);
    toast(res.warnings.length ? res.warnings.join(" · ") : "Updated from DVLA/DVSA");
    render();
  } catch (err) { toast(err.message); }
}

async function saveDetails(e) {
  e.preventDefault();
  try {
    const res = await api("PATCH", `/api/vehicles/${state.vehicle.id}`, formData(e.target));
    if (!res.queued) toast("Saved");
    render();
  } catch (err) { toast(err.message); }
}

async function addMileage(e) {
  e.preventDefault();
  await api("POST", `/api/vehicles/${state.vehicle.id}/mileage`, formData(e.target));
  render();
}

async function deleteMileage(id) { await api("DELETE", `/api/mileage/${id}`); render(); }

async function deleteRecord(id) {
  if (!confirm("Delete this record?")) return;
  await api("DELETE", `/api/records/${id}`);
  render();
}

async function deleteVehicle(id) {
  if (!confirm("Delete this vehicle and all its history?")) return;
  await api("DELETE", `/api/vehicles/${id}`);
  location.hash = "#/";
}

// assistant

const chat = { id: null, busy: false };
const chatLog = $("#chat-log");
const chatInput = $("#chat-input");

const HINTS = [
  "What's due across all my vehicles in the next 3 months?",
  "I had the Golf serviced today at 61,200 miles: oil, oil filter and air filter. £189 at KwikFit.",
  "What does the van need at its next service?",
  "My insurance on the Fiesta renews on 3 March with Admiral.",
];

// Without a paid API key, Rin hands questions to the free Claude or ChatGPT websites instead,
// pre-filled with a garage summary. The summary never includes plates, policy numbers or notes.
const FREE_HINTS = [
  "What's overdue?",
  "What's due at the BMW's next service?",
  "When is the Subaru's insurance renewal and what's the excess?",
  "When is brake fluid due?",
];

function setChatMode(paid) {
  chat.free = !paid;
  $("#chat-ctx-row").hidden = paid;
  $("#chat-input").placeholder = paid ? "Ask Rin, or tell her what work you've done…" : "Ask Rin about your vehicles…";
}

function garageSummary() {
  const vs = state.dashboard?.vehicles || [];
  const st = (s) => (s.status === "sorn" ? "SORN" : s.date ? `${fmtDate(s.date)} (${STATUS_TEXT[s.status].toLowerCase()})` : "not set");
  const lines = vs.map((v) => {
    const s = v.status;
    const name = [v.year, v.make, v.model].filter(Boolean).join(" ") + (v.variant ? ` (${v.variant})` : "");
    const svc = s.service.status === "unknown" ? "no service logged"
      : `next service ${fmtDate(s.service.projected_date)} (${STATUS_TEXT[s.service.status].toLowerCase()})`;
    const odo = s.mileage.stale ? "no recent mileage" : `about ${miles(s.mileage.estimated_now)}`;
    return `- ${name}${v.fuel ? `, ${v.fuel}` : ""}${s.tax.status === "sorn" ? ", SORN (off the road)" : ""}. MOT ${st(s.mot)}; tax ${st(s.tax)}; ${svc}; ${odo}.`;
  });
  const v = location.hash.startsWith("#/v/") ? state.vehicle : null;
  if (v) {
    const ev = v.eval;
    lines.push("", `Currently looking at the ${[v.year, v.make, v.model].filter(Boolean).join(" ")}:`);
    if (ev.next_service_items.length)
      lines.push("Due at its next service: " + ev.next_service_items.map((i) => i.name).join("; ") + ".");
    if (ev.latest_mot_advisories.length)
      lines.push(`Last MOT (${fmtDate(ev.latest_mot_date)}) notes: ` + ev.latest_mot_advisories.map((d) => `${d.type.toLowerCase()}: ${d.text}`).join("; ") + ".");
    const recent = v.records.slice(0, 5).map((r) => `${fmtDate(r.date)} ${r.kind}${r.items.length ? ` (${r.items.join(", ")})` : ""}`);
    if (recent.length) lines.push("Recent work: " + recent.join("; ") + ".");
  }
  return lines.join("\n");
}

// Rin's free brain: answers questions from the garage data already in the app.

const ALIASES = { volkswagen: ["vw"], "n-box": ["nbox", "n box"], "tt-r250": ["ttr", "ttr250"], "yzf-r7": ["r7"],
  "alfa romeo": ["alfa"], "boxster s": ["boxster"], m135i: ["m135", "1 series"] };
const STOP = new Set(["the", "and", "se", "mk", "gti", "jtdm", "d"]);
const ITEM_TERMS = [
  ["brake fluid"], ["coolant", "antifreeze"], ["cambelt", "timing belt", "timing chain"], ["spark plug", "plugs"],
  ["air filter"], ["cabin filter", "pollen", "microfilter"], ["fuel filter"], ["oil filter"], ["cvt"],
  ["gearbox", "transmission", "diff"], ["battery"], ["valve"], ["chain"], ["tyre", "tire"], ["wiper"],
  ["air-con", "aircon", "air con"], ["ims"], ["glow plug"], ["drive belt", "aux belt", "auxiliary"], ["fork"], ["steering head"],
];
const norm = (t) => (t || "").toLowerCase();
const compact = (t) => norm(t).replace(/[^a-z0-9]/g, "");

function vehiclesIn(q) {
  const words = new Set(norm(q).split(/[^a-z0-9]+/));
  const flat = compact(q);
  return (state.dashboard?.vehicles || []).filter((v) => {
    const names = [v.make, v.model, v.reg].filter(Boolean).map(norm);
    const tokens = names.join(" ").split(/[^a-z0-9]+/).filter((t) => t.length >= 2 && !STOP.has(t));
    const extra = names.flatMap((n) => ALIASES[n] || []);
    const joined = [compact(v.model), compact(v.reg), compact(`${v.make}${v.model}`)].filter((t) => t.length >= 3);
    return tokens.some((t) => words.has(t)) || [...extra, ...joined].some((a) => flat.includes(compact(a)));
  });
}

function itemTerm(q) {
  const t = norm(q);
  return ITEM_TERMS.find((alts) => alts.some((a) => t.includes(a)));
}

const when = (s) => (s.status === "sorn" ? "not needed (SORN)" : s.date ? `${fmtDate(s.date)} (${rel(s.days_left)})` : "not set in the app");
const vname = (v) => esc(v.label || [v.make, v.model].join(" "));
const insLine = (v) => (v.notes || "").split("\n").find((l) => l.startsWith("Insurance:"));

async function detailOf(v) {
  if (state.vehicle?.id === v.id) return state.vehicle;
  return api("GET", `/api/vehicles/${v.id}`);
}

function serviceText(v, s) {
  if (s.status === "unknown") return `No service logged for the ${vname(v)} yet, so I can't say when the next one's due.`;
  const last = s.last ? ` Last one was ${fmtDate(s.last.date)}${s.last.miles ? ` at ${miles(s.last.miles)}` : ""}.` : "";
  const by = s.due_miles ? ` or at ${miles(s.due_miles)}` : "";
  return `The ${vname(v)}'s next service is due <b>${fmtDate(s.projected_date)}</b>${by} (${rel(s.days_left)}).${last}`;
}

async function localAnswer(q) {
  const t = norm(q);
  const vs = vehiclesIn(q);
  const term = itemTerm(q);
  const has = (re) => re.test(t);
  const out = [];

  if (has(/\b(i|i've|ive|we|just)\b.*\b(changed|replaced|fitted|did|done|swapped|put (in|on)|topped|serviced)\b/) && vs.length === 1) {
    return { html: `Nice work! I can't log things from chat for free, but you can log it on the ${vname(vs[0])} here:`,
             action: { label: `Log work on the ${vs[0].label}`, vehicle: vs[0].id } };
  }

  if (has(/pressure|\bpsi\b|\bbar\b|inflate|pump (the )?tyres/)) {
    const pool = vs.length ? vs : state.dashboard.vehicles;
    return { html: pool.map((v) => v.tyre_front_psi == null && v.tyre_rear_psi == null
      ? `<b>${vname(v)}</b>: pressures not set yet (add them in Details).`
      : `<b>${vname(v)}</b>: front <b>${psiTxt(v.tyre_front_psi)}</b> psi (${bar(v.tyre_front_psi)} bar), rear <b>${psiTxt(v.tyre_rear_psi)}</b> psi (${bar(v.tyre_rear_psi)} bar)` +
        (v.tyre_front_laden_psi != null || v.tyre_rear_laden_psi != null ? `; fully loaded ${psiTxt(v.tyre_front_laden_psi ?? v.tyre_front_psi)} / ${psiTxt(v.tyre_rear_laden_psi ?? v.tyre_rear_psi)} psi` : "") +
        (v.tyre_size ? ` · ${esc(v.tyre_size)}` : "") + ".").join("\n") + "\n\nCheck them cold, before driving far." };
  }

  // opinions and how-tos are better answered by Claude or ChatGPT
  if (has(/\b(best|recommend|should i|how (do|to|can)|why|which|worth|advice|good idea|compare)\b/))
    return { advice: true };

  // a specific maintenance item, e.g. "when is brake fluid due on the BMW?"
  if (term) {
    const pool = vs.length ? vs : (state.dashboard?.vehicles || []).filter((v) => v.status.tax.status !== "sorn");
    for (const v of pool) {
      const d = await detailOf(v);
      let hits = d.eval.items.filter((i) => term.some((a) => norm(i.name).includes(a)));
      if (hits.some((i) => !i.every_service)) hits = hits.filter((i) => !i.every_service);  // skip "inspection (…tyres…)" noise
      for (const i of hits) {
        const last = i.last_done ? `last done ${fmtDate(i.last_done.date)}` : "no record of it being done";
        const next = i.every_service ? "done at every service" : i.due ? `next due <b>${fmtDate(i.due.projected_date)}</b>${i.due.due_miles ? ` or ${miles(i.due.due_miles)}` : ""} (${STATUS_TEXT[i.due.status].toLowerCase()})` : "no interval set";
        out.push(`<b>${vname(v)}</b>, ${esc(i.name)}: ${last}, ${next}.`);
      }
    }
    if (out.length) return { html: out.join("\n") + (vs.length ? "" : "\n\n(SORN vehicles left out. Name one to include it.)") };
  }

  const wantsDue = has(/overdue|what'?s due|due soon|coming up|to ?do|need(s)? doing|what needs/);
  if (!vs.length && wantsDue) {
    const ev = state.dashboard.upcoming;
    const over = ev.filter((e) => e.status === "overdue"), soon = ev.filter((e) => e.status !== "overdue");
    if (!ev.length) return { html: "Nothing's due in the next 4 months. Garage is all clear ✨" };
    if (over.length) out.push("<b>Overdue:</b>\n" + over.map((e) => `• ${esc(e.vehicle)}: ${esc(e.title)} (${rel(e.days_left)})`).join("\n"));
    if (soon.length) out.push("<b>Coming up:</b>\n" + soon.slice(0, 6).map((e) => `• ${esc(e.vehicle)}: ${esc(e.title)}, ${fmtDate(e.date)} (${rel(e.days_left)})`).join("\n"));
    return { html: out.join("\n\n") };
  }

  for (const v of vs) {
    const s = v.status;
    const topic = [];
    if (has(/\bmot\b/) && !has(/advis|fail|defect|history/)) topic.push(`The ${vname(v)}'s MOT: ${when(s.mot)}.`);
    if (has(/\btax/)) topic.push(`The ${vname(v)}'s road tax: ${when(s.tax)}.`);
    if (has(/insur|excess|policy|renew|cover/)) {
      const line = insLine(v);
      topic.push(line ? `<b>${vname(v)}</b> ${esc(line)}` : `The ${vname(v)}'s insurance renewal: ${when(s.insurance)}${v.insurance_provider ? ` with ${esc(v.insurance_provider)}` : ""}.`);
    }
    if (has(/advis|fail|defect|mot history/)) {
      const d = await detailOf(v);
      const adv = d.eval.latest_mot_advisories;
      topic.push(adv.length ? `From the last MOT (${fmtDate(d.eval.latest_mot_date)}):\n` + adv.map((a) => `• ${esc(a.type.toLowerCase())}: ${esc(a.text)}`).join("\n")
                            : `No advisories on the ${vname(v)}'s last MOT${d.eval.latest_mot_date ? ` (${fmtDate(d.eval.latest_mot_date)})` : ""}.`);
    }
    if (has(/mileage|miles|odometer|\bkm\b|how far/)) {
      const m = s.mileage;
      topic.push(m.stale ? (m.last_reading ? `Last reading I have is ${miles(m.last_reading.miles)} on ${fmtDate(m.last_reading.date)}. Add a current one in the Mileage tab.` : "No mileage readings yet.")
                         : `About ${miles(m.estimated_now)} now, doing roughly ${miles(m.annual_miles)} a year.`);
    }
    if (has(/servic|oil/) || wantsDue) {
      topic.push(serviceText(v, s.service));
      const d = await detailOf(v);
      const items = d.eval.next_service_items;
      if (items.length && (wantsDue || has(/what|need|include|do at/)))
        topic.push("At that service:\n" + items.map((i) => `• ${esc(i.name)}${i.reason === "early" ? " (do early)" : ""}`).join("\n"));
    }
    if (!topic.length) {
      topic.push(`<b>${vname(v)}</b>: MOT ${when(s.mot)}; tax ${when(s.tax)}; insurance ${when(s.insurance)}.`, serviceText(v, s.service));
    }
    out.push(topic.join("\n"));
  }
  if (out.length) return { html: out.join("\n\n") };
  return null;
}

function externalPrompt(question, withGarage) {
  let prompt = "You're Rin, a friendly, knowledgeable mechanic helping a UK owner look after their vehicles. " +
    "They service every vehicle every 12 months or 5,000 miles, whichever comes first. " +
    "Answer concisely and practically, and flag anything safety-related.\n\n";
  if (withGarage) prompt += `Their garage, as of ${fmtDate(today())}:\n${garageSummary()}\n\n`;
  prompt += `Question: ${question}`;
  return prompt.length > 6000 ? prompt.slice(0, 5800) + `\n…\n\nQuestion: ${question}` : prompt;
}

function chatWelcome() {
  const a = state.dashboard?.assistant;
  const free = a && !a.ok;
  const hints = free ? FREE_HINTS : HINTS;
  chatLog.innerHTML = free
    ? `<div class="msg bot">Yo! I'm Rin 🔧 Ask me about your vehicles: MOT, tax, insurance, services, what's due, MOT advisories, mileage. I'll answer from what's in the app.\n\nFor anything else, I'll pass your question to <b>Claude</b> or <b>ChatGPT</b> (both free). To log work, use <b>Log work</b> on a vehicle.</div>
       <div class="hints">${hints.map((h) => `<button type="button">${esc(h)}</button>`).join("")}</div>`
    : `<div class="msg bot">Yo! I'm Rin, your garage mechanic 🔧 Ask me what's due, or tell me what work you've done and I'll log it.</div>
       <div class="hints">${hints.map((h) => `<button type="button">${esc(h)}</button>`).join("")}</div>`;
  for (const b of chatLog.querySelectorAll(".hints button")) b.onclick = () => { chatInput.value = b.textContent; chatInput.focus(); };
}

function addMsg(cls, text) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.textContent = text;
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return el;
}

function openChat(prefill) {
  document.body.classList.add("chat-open");
  if (!chatLog.children.length) chatWelcome();
  if (prefill) chatInput.value = prefill;
  chatInput.focus();
}

function handOff(to, question) {
  const prompt = externalPrompt(question, $("#chat-ctx").checked);
  const url = to === "chatgpt"
    ? `https://chatgpt.com/?q=${encodeURIComponent(prompt)}`
    : `https://claude.ai/new?q=${encodeURIComponent(prompt)}`;
  window.open(url, "_blank", "noopener");
}

async function sendChat(e) {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || chat.busy) return;
  if (chat.free) {
    chatLog.querySelector(".hints")?.remove();
    chatInput.value = "";
    addMsg("user", text);
    let ans = null;
    try { ans = await localAnswer(text); } catch (err) { ans = null; }
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = ans?.advice
      ? "That's more of an advice question, so Claude or ChatGPT will answer it better. I'll send them your garage details too."
      : ans ? ans.html : "I couldn't find that in your garage data. Want me to ask someone smarter?";
    if (ans?.advice) ans = null;
    const row = document.createElement("div");
    row.className = "handoff";
    if (ans?.action) {
      const b = document.createElement("button");
      b.className = "primary"; b.textContent = ans.action.label;
      b.onclick = () => { location.hash = `#/v/${ans.action.vehicle}`; setTimeout(() => state.vehicle && logWork(), 400); };
      row.appendChild(b);
    }
    for (const [to, label] of [["claude", ans ? "Ask Claude more" : "Ask Claude"], ["chatgpt", "Ask ChatGPT"]]) {
      const b = document.createElement("button");
      b.textContent = label;
      b.onclick = () => handOff(to, text);
      row.appendChild(b);
    }
    el.appendChild(row);
    chatLog.appendChild(el);
    chatLog.scrollTop = chatLog.scrollHeight;
    return;
  }
  chatLog.querySelector(".hints")?.remove();
  chatInput.value = "";
  addMsg("user", text);
  const pending = addMsg("bot typing", "Working on it");
  chat.busy = true;
  try {
    const res = await api("POST", "/api/chat", { conversation_id: chat.id, message: text });
    chat.id = res.conversation_id;
    pending.remove();
    if (res.actions.length) {
      const acts = document.createElement("div");
      acts.className = "acts";
      acts.innerHTML = res.actions.map((a) => `<div>${esc(a)}</div>`).join("");
      chatLog.appendChild(acts);
    }
    addMsg("bot", res.reply || "(no reply)");
    if (res.changed) render();
  } catch (err) {
    pending.remove();
    addMsg("bot error", err.message);
  } finally {
    chat.busy = false;
  }
}

$("#chat-form").addEventListener("submit", sendChat);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#chat-btn").onclick = () => (document.body.classList.contains("chat-open") ? document.body.classList.remove("chat-open") : openChat());
$("#chat-close").onclick = () => document.body.classList.remove("chat-open");
$("#chat-new").onclick = async () => {
  if (chat.id) await api("POST", "/api/chat/reset", { conversation_id: chat.id }).catch(() => {});
  chat.id = null;
  chatWelcome();
};
$("#add-btn").onclick = addVehicle;
$("#scan-input").onchange = (e) => { scanDocument(e.target.files[0]); e.target.value = ""; };
$("#sync-badge").onclick = () => flushQueue().then(render);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

// home-screen apps stay open for days; reload when the app has been updated on the Mac
let appVersion = null;
async function checkForUpdate() {
  try {
    const { version } = await fetch("/api/version", { cache: "no-store" }).then((r) => r.json());
    if (appVersion && version !== appVersion && !dialog.open) {
      await navigator.serviceWorker?.getRegistration().then((r) => r?.update()).catch(() => {});
      location.reload();
    }
    appVersion = version;
  } catch { /* offline: keep what we have */ }
}
checkForUpdate();
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkForUpdate(); });
setInterval(checkForUpdate, 5 * 60 * 1000);
window.addEventListener("online", flushQueue);
setInterval(flushQueue, 30000);
flushQueue();
updateSyncBadge();

render();
