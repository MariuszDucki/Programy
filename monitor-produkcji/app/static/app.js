"use strict";
const $ = (s) => document.querySelector(s);
const pct = (x) => (x * 100).toFixed(1) + "%";
const fmtInt = (n) => Number(n).toLocaleString("pl-PL");
const hm = (ts) => new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
const dur = (s) => {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cls = (oee, target) => (oee >= target ? "good" : oee >= target * 0.85 ? "mid" : "bad");
const catColor = { run: "var(--run)", unplanned: "var(--unplanned)", planned: "var(--planned)" };

let CFG = null, SNAP = null, openLine = null, clockSkew = 0;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ------------------------------------------------------------ widok hali
function renderDash(s) {
  SNAP = s;
  clockSkew = s.ts - Date.now() / 1000;
  $("#plant").textContent = s.plant;
  $("#shift").textContent = `Zmiana ${s.shift.name} · ${hm(s.shift.start)}–${hm(s.shift.end)}`;

  const L = s.lines;
  const good = L.reduce((a, l) => a + l.good, 0), scrap = L.reduce((a, l) => a + l.scrap, 0);
  const avgOee = L.length ? L.reduce((a, l) => a + l.oee, 0) / L.length : 0;
  const running = L.filter((l) => l.state === "RUN").length;
  $("#summary").innerHTML = [
    ["OEE hali (średnia)", pct(avgOee)],
    ["Linie w ruchu", `${running} / ${L.length}`],
    ["Sztuki dobre", fmtInt(good)],
    ["Braki", `${fmtInt(scrap)} (${good + scrap ? pct(scrap / (good + scrap)) : "0%"})`],
    ["Aktywne alarmy", s.alarms.length],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  $("#grid").innerHTML = L.map((l) => {
    const now = Date.now() / 1000 + clockSkew;
    const progress = l.target_units_shift ? Math.min(1, l.good / l.target_units_shift) : 0;
    const expected = l.target_units_shift ? Math.min(1, l.expected_units_now / l.target_units_shift) : 0;
    const needReason = l.category === "unplanned" && !l.reason;
    return `<div class="tile" data-id="${esc(l.id)}">
      <div class="head cat-${l.category}">
        <span class="name">${esc(l.name)}</span>
        <span class="since" data-since="${l.state_since}">${esc(l.state_label)} · ${dur(now - l.state_since)}</span>
      </div>
      <div class="body">
        <div class="prod">${esc(l.product)}</div>
        <div class="oee-row">
          <span class="oee-big ${cls(l.oee, l.target_oee)}">${pct(l.oee)}</span>
          <span>OEE<br><small style="color:var(--muted)">cel ${pct(l.target_oee)}</small></span>
          <span style="margin-left:auto;text-align:right"><b>${l.speed_per_min}</b>/min<br>
          <small style="color:var(--muted)">nominał ${l.ideal_rate_per_min}</small></span>
        </div>
        <div class="apq">
          <div><b>${pct(l.availability)}</b><span>Dostępność</span></div>
          <div><b>${pct(l.performance)}</b><span>Wydajność</span></div>
          <div><b>${pct(l.quality)}</b><span>Jakość</span></div>
        </div>
        <div class="bar" title="Wykonanie planu zmiany (kreska = gdzie powinniśmy być teraz)">
          <i style="width:${progress * 100}%"></i><em style="left:${expected * 100}%"></em></div>
        <div class="row"><span>Dobre: ${fmtInt(l.good)} / plan ${fmtInt(l.target_units_shift)}</span>
          <span>Braki: ${fmtInt(l.scrap)}</span></div>
        ${needReason ? `<div class="reason">⚠ Postój bez przypisanej przyczyny – kliknij, aby uzupełnić</div>`
          : l.reason ? `<div class="reason">Przyczyna: ${esc(l.reason)}</div>` : ""}
      </div></div>`;
  }).join("");
  document.querySelectorAll(".tile").forEach((t) => (t.onclick = () => showDetail(t.dataset.id)));

  renderAlarmBar(s.alarms);
}

function renderAlarmBar(alarms) {
  const bar = $("#alarmbar");
  bar.classList.toggle("hidden", !alarms.length);
  bar.innerHTML = alarms.map((a) => `<span class="a ${a.acked_ts ? "acked" : ""}">🔔 ${esc(a.message)}
    ${a.acked_ts ? "<small>(potwierdzony)</small>" : `<button data-ack="${a.id}">Potwierdź</button>`}</span>`).join("");
  bar.querySelectorAll("[data-ack]").forEach((b) => (b.onclick = async () => {
    await api(`/api/alarms/${b.dataset.ack}/ack`, { method: "POST", body: JSON.stringify({ user: "operator" }) });
    b.parentElement.classList.add("acked"); b.remove();
  }));
}

// odświeżanie liczników czasu co sekundę bez czekania na serwer
setInterval(() => {
  const now = Date.now() / 1000 + clockSkew;
  $("#clock").textContent = new Date(now * 1000).toLocaleTimeString("pl-PL");
  document.querySelectorAll("[data-since]").forEach((el) => {
    el.textContent = el.textContent.replace(/·.*$/, "· " + dur(now - Number(el.dataset.since)));
  });
}, 1000);

// ------------------------------------------------------------ szczegóły linii
async function showDetail(id) {
  openLine = id;
  $("#drawer").classList.remove("hidden");
  await refreshDetail();
}
async function refreshDetail() {
  if (!openLine) return;
  const d = await api(`/api/lines/${openLine}`);
  const { shift, now, kpi } = d;
  const span = shift.end - shift.start;
  const tl = d.timeline.map((r) => {
    const s = Math.max(r.start_ts, shift.start), e = Math.min(r.end_ts ?? now, shift.end);
    const cat = CFG.states[r.state].category;
    return `<div title="${esc(r.label)} ${hm(s)}–${hm(e)} (${dur(e - s)})${r.reason ? " · " + esc(r.reason) : ""}"
      style="width:${((e - s) / span) * 100}%;background:${catColor[cat]}"></div>`;
  }).join("");
  const remaining = Math.max(0, shift.end - now);
  const hours = Math.round(span / 3600);
  const target = d.line.ideal_rate_per_min * 60 * (d.line.target_oee ?? 0.75);
  const byH = Object.fromEntries(d.hourly.map((h) => [h.h, h]));
  const maxV = Math.max(target, ...d.hourly.map((h) => h.good + h.scrap), 1);
  const cols = [...Array(hours).keys()].map((i) => {
    const h = byH[i]; const v = h ? h.good : 0;
    return `<div class="col"><span>${v ? fmtInt(v) : ""}</span>
      <i class="${v < target && i * 3600 + shift.start + 3600 <= now ? "under" : ""}" style="height:${(v / maxV) * 100}%"></i></div>`;
  }).join("");
  const xs = [...Array(hours).keys()].map((i) => `<span>${hm(shift.start + i * 3600)}</span>`).join("");
  const reasons = CFG.stop_reasons;
  const stops = d.stops.map((r) => `<tr>
      <td><span class="chip" style="background:${catColor[r.category]}">${esc(r.label)}</span></td>
      <td>${hm(r.start_ts)}</td><td>${r.end_ts ? hm(r.end_ts) : "trwa"}</td>
      <td class="num">${dur(r.duration_s)}</td>
      <td>${r.category === "unplanned" ? `<select data-stop="${r.id}"><option value="">— wybierz —</option>
        ${reasons.map((x) => `<option ${x === r.reason ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>`
        : esc(r.reason || "")}</td></tr>`).join("");

  $("#detail").innerHTML = `
    <h2>${esc(d.line.name)}</h2>
    <div class="summary">
      <div class="kpi"><div class="v ${cls(kpi.oee, d.line.target_oee)}">${pct(kpi.oee)}</div><div class="l">OEE zmiany</div></div>
      <div class="kpi"><div class="v">${pct(kpi.availability)}</div><div class="l">Dostępność</div></div>
      <div class="kpi"><div class="v">${pct(kpi.performance)}</div><div class="l">Wydajność</div></div>
      <div class="kpi"><div class="v">${pct(kpi.quality)}</div><div class="l">Jakość</div></div>
      <div class="kpi"><div class="v">${dur(kpi.unplanned_stop_s)}</div><div class="l">Postoje nieplanowane</div></div>
      <div class="kpi"><div class="v">${dur(remaining)}</div><div class="l">Do końca zmiany</div></div>
    </div>
    <h3>Oś czasu zmiany ${esc(shift.name)}</h3>
    <div class="timeline">${tl}</div>
    <div class="tl-axis"><span>${hm(shift.start)}</span><span>teraz ${hm(now)}</span><span>${hm(shift.end)}</span></div>
    <h3>Produkcja godzinowa (szt. dobre, cel ${fmtInt(Math.round(target))}/h)</h3>
    <div class="hourly">${cols}</div><div class="hourly-x">${xs}</div>
    <h3>Postoje – przypisz przyczyny</h3>
    <table class="tbl"><tr><th>Stan</th><th>Od</th><th>Do</th><th>Czas</th><th>Przyczyna</th></tr>${stops ||
      '<tr><td colspan="5">Brak postojów na tej zmianie 👍</td></tr>'}</table>`;
  document.querySelectorAll("[data-stop]").forEach((sel) => (sel.onchange = async () => {
    await api(`/api/stops/${sel.dataset.stop}/reason`, { method: "PUT", body: JSON.stringify({ reason: sel.value }) });
  }));
}
$("#drawer-close").onclick = () => { openLine = null; $("#drawer").classList.add("hidden"); };
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer-close").onclick(); };
setInterval(() => { if (openLine && !document.activeElement?.matches("select")) refreshDetail(); }, 5000);

// ------------------------------------------------------------ raport
async function loadReport() {
  const date = $("#r-date").value, shift = $("#r-shift").value;
  const q = `?date=${date}&shift=${encodeURIComponent(shift)}`;
  $("#r-csv").href = "/api/report.csv" + q;
  const r = await api("/api/report" + q);
  $("#report").innerHTML = `
    <table class="tbl"><tr><th>Linia</th><th>OEE</th><th>Dostępność</th><th>Wydajność</th><th>Jakość</th>
      <th>Dobre</th><th>Braki</th><th>Praca</th><th>Postoje nieplan.</th><th>Postoje plan.</th></tr>
    ${r.lines.map((l) => { const k = l.kpi; return `<tr><td>${esc(l.name)}</td>
      <td class="num">${pct(k.oee)}</td><td class="num">${pct(k.availability)}</td><td class="num">${pct(k.performance)}</td>
      <td class="num">${pct(k.quality)}</td><td class="num">${fmtInt(k.good)}</td><td class="num">${fmtInt(k.scrap)}</td>
      <td class="num">${dur(k.run_s)}</td><td class="num">${dur(k.unplanned_stop_s)}</td><td class="num">${dur(k.planned_stop_s)}</td></tr>`; }).join("")}
    </table>
    ${r.lines.map((l) => {
      const max = Math.max(1, ...l.stop_pareto.map((p) => p[1]));
      return `<h3>Pareto postojów – ${esc(l.name)}</h3>${l.stop_pareto.length ? `<div class="pareto">${l.stop_pareto.map(([n, m]) =>
        `<span>${esc(n)}</span><i style="width:${(m / max) * 100}%"></i><span>${m} min</span>`).join("")}</div>` : "<p>Brak postojów nieplanowanych.</p>"}`;
    }).join("")}`;
}
$("#r-load").onclick = loadReport;

// ------------------------------------------------------------ alarmy (historia)
async function loadAlarms() {
  const a = await api("/api/alarms");
  $("#alarm-tbl").innerHTML = `<tr><th>Wystąpił</th><th>Linia</th><th>Typ</th><th>Opis</th><th>Ustąpił</th><th>Potwierdził</th></tr>` +
    a.map((x) => `<tr><td>${new Date(x.raised_ts * 1000).toLocaleString("pl-PL")}</td><td>${esc(x.line_id)}</td>
      <td>${esc(x.kind)}</td><td>${esc(x.message)}</td><td>${x.cleared_ts ? hm(x.cleared_ts) : "<b>aktywny</b>"}</td>
      <td>${x.acked_by ? esc(x.acked_by) + " " + hm(x.acked_ts) : ""}</td></tr>`).join("");
}

// ------------------------------------------------------------ nawigacja
document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + b.dataset.view));
  if (b.dataset.view === "report") loadReport();
  if (b.dataset.view === "alarms") loadAlarms();
}));

// ------------------------------------------------------------ połączenie na żywo
function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => $("#conn").classList.add("ok");
  ws.onmessage = (ev) => renderDash(JSON.parse(ev.data));
  ws.onclose = () => { $("#conn").classList.remove("ok"); setTimeout(connect, 3000); };
}

(async function init() {
  CFG = await api("/api/config");
  $("#r-shift").innerHTML = CFG.shifts.map((s) => `<option>${esc(s.name)}</option>`).join("");
  const s = await api("/api/snapshot");
  const d = new Date(s.shift.start * 1000);
  $("#r-date").value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  $("#r-shift").value = s.shift.name;
  renderDash(s);
  connect();
})();
