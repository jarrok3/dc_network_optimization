"use strict";

// Presentation only: all routing, assignments, costs and uncertainty are saved results.
const $ = id => document.getElementById(id);
const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const finite = x => typeof x === "number" && Number.isFinite(x);
const number = (x, digits = 0) => finite(x) ? x.toLocaleString("en-US", {maximumFractionDigits: digits, minimumFractionDigits: digits}) : "—";
const millions = (x, digits = 1) => finite(x) ? number(x / 1e6, digits) : "—";
const money = x => finite(x) ? millions(x) + " M" : "—";
const percent = (x, digits = 2) => finite(x) ? number(100 * x, digits) + "%" : "—";
const sum = values => values.reduce((a, b) => a + (finite(b) ? b : 0), 0);
const fleet = x => x?.fleet ? sum(Object.values(x.fleet)) : null;
// Golden-angle hues keep site colors stable across networks without repeating every 20 IDs.
const siteColor = id => `hsl(${((Number(String(id).replace(/\D/g, "")) || 0) * 137.508 + 170) % 360} 48% 42%)`;
const costParts = [["dc_fixed", "DC fixed", "#244763"], ["vehicle_fixed", "Owned fleet", "#6c67d9"], ["fuel", "Fuel", "#38a59b"], ["spot", "Spot", "#e1ae48"], ["failure", "Failure", "#d77770"]];
let bundle, evaluation, networkKey = "", networkRequest = 0, sourceRequest = 0;
let storeIndex = new Map(), siteIndex = new Map(), mapBox = [0, 0, 700, 560], rawSource = "";

async function getJSON(url) {
  const response = await fetch(url, {cache: "no-store"});
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).error || message; } catch (_) { /* HTTP error without JSON */ }
    throw new Error(message || "HTTP " + response.status);
  }
  return response.json();
}
function kpi(value, label, detail = "") {
  return `<div class="kpi"><strong>${esc(value)}</strong><span>${esc(label)}</span>${detail ? `<small>${esc(detail)}</small>` : ""}</div>`;
}
function statusPill(row) {
  if (row?.feasible === true) return '<span class="status-pill">Service-feasible</span>';
  if (row?.status === "infeasible") return '<span class="status-pill fail">Assignment infeasible</span>';
  if (row?.feasible === false) return '<span class="status-pill fail">Service target failed</span>';
  return '<span class="status-pill unknown">' + esc(row?.status || "Not evaluated") + "</span>";
}
function table(headers, rows) {
  return `<table><thead><tr>${headers.map(x => `<th scope="col">${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${headers.length}">No saved observations.</td></tr>`}</tbody></table>`;
}
function action(label, key) {
  return `<button class="cell-button" data-network="${esc(key)}" type="button">${esc(label)}</button>`;
}
function networkSummary(key) { return bundle.networks.find(n => n.id === key); }
function showStatus(text, error = false) {
  $("status").textContent = text;
  $("status").className = "notice" + (error ? " error" : " neutral");
}
async function loadDashboard() {
  $("refresh").disabled = true;
  try {
    bundle = await getJSON("/api/dashboard");
    storeIndex = new Map(bundle.geography.stores.map(s => [s.id, s]));
    siteIndex = new Map(bundle.geography.sites.map(s => [s.id, s]));
    renderOverview(); renderNetworkOptions(); renderComparison(); renderSweep(); renderSearch(); renderSources();
    $("dashboard").hidden = false;
    const initial = bundle.networks.some(n => n.id === networkKey) ? networkKey : (networkSummary("recommended") ? "recommended" : bundle.networks[0]?.id);
    if (initial) await selectNetwork(initial, false);
    else renderEmptyNetwork();
    showStatus("Saved run: " + (bundle.report.status || "unknown") + " · Last saved " + new Date(bundle.modified * 1000).toLocaleString() + ". Viewing these results does not run optimization.");
  } catch (error) {
    showStatus("Unable to load the saved run: " + error.message + ". Launch run_demo.py with venv_midterm and open the localhost URL it prints.", true);
  } finally { $("refresh").disabled = false; }
}

function renderOverview() {
  const r = bundle.report, n = networkSummary("recommended"), h = networkSummary("holdout:recommended");
  const nStores = r.scope?.n_stores ?? r.preflight?.n_stores;
  $("run-summary").textContent = `${number(nStores)} active stores · ${number(r.preflight?.n_sites ?? bundle.geography.sites.length)} candidate sites · seed ${r.settings?.seed ?? "—"}`;
  $("scope-badge").textContent = r.scope?.kind || "Saved run";
  $("kpis").innerHTML = kpi(number(n?.k), "Recommended distribution centres", "Best service-feasible design found") + kpi(number(fleet(n)), "Owned vehicles", "Fleet sized on D28") + kpi(money(n?.total), "KRW / month · D28", "Final sampled-day evaluation") + kpi(percent(h?.service_min_day), "Worst-day before-noon service · H14", "98% service target");
  $("overview-note").textContent = r.recommendation_scope || r.reason || "No recommendation is recorded for this run.";
  $("overview-facts").innerHTML = `<div class="fact"><strong>${number(nStores)} active stores, not a toy subset</strong><p>Coordinates and site limits come from the nationwide input tables. The model instance is synthetic.</p></div><div class="fact"><strong>Search → final selection → holdout</strong><p>D7 guides candidate selection; D28 ranks finalists and sizes fleets; H14 tests the selected design with D28 fleets fixed.</p></div><div class="fact"><strong>${number(r.settings?.pool_size)}-candidate pool · ${number(r.settings?.budget)} evaluations per rule</strong><p>No global optimality certificate. Saved costs and service figures are shown without rerouting or refitting.</p></div>`;
}
function renderNetworkOptions() {
  const groups = [["Main results", n => ["recommended", "baseline", "reassigned", "average"].includes(n.id) || n.id.startsWith("holdout:")], ["D28 finalists", n => n.id.startsWith("final:")], ["Guided D7 evaluations", n => n.id.startsWith("guided:")], ["Random-control D7 evaluations", n => n.id.startsWith("random:")]];
  $("network-select").innerHTML = groups.map(([name, match]) => `<optgroup label="${esc(name)}">${bundle.networks.filter(match).map(n => `<option value="${esc(n.id)}">${esc(n.label)}${finite(n.total) ? " · " + money(n.total) : " · " + esc(n.status)}${n.feasible === false ? " · not service-feasible" : ""}</option>`).join("")}</optgroup>`).join("");
}
async function selectNetwork(key, scroll = true) {
  const token = ++networkRequest;
  $("network-select").value = key;
  $("network-notice").hidden = false;
  $("network-notice").textContent = "Loading saved evaluation…";
  try {
    const result = await getJSON("/api/network?id=" + encodeURIComponent(key));
    if (token !== networkRequest) return;
    evaluation = result.evaluation; networkKey = key;
    $("store-detail").textContent = "Hover over a store for its ID, region and assigned site.";
    $("network-select").value = key;
    $("site-filter").innerHTML = '<option value="">All open DCs</option>' + (evaluation.sites || []).map(s => `<option>${esc(s)}</option>`).join("");
    $("network-notice").hidden = !evaluation.reason;
    $("network-notice").className = "notice" + (evaluation.feasible === false ? " error" : "");
    $("network-notice").textContent = evaluation.reason || "";
    $("network-stats").innerHTML = kpi(money(evaluation.total), "Monthly cost · M KRW") + kpi(percent(evaluation.service_min_day), "Minimum sampled-day service") + kpi(number(fleet(evaluation)), "Owned fleet", evaluation.fleet_source || "") + kpi(number(Object.keys(evaluation.store_to_site || {}).length), "Assigned active stores", evaluation.assignment_mode || "No saved assignment");
    mapBox = [0, 0, 700, 560]; renderMap(); renderSites(); setupDays();
    if (scroll) $("network").scrollIntoView({behavior: "smooth"});
  } catch (error) {
    if (token !== networkRequest) return;
    $("network-notice").hidden = false; $("network-notice").className = "notice error";
    $("network-notice").textContent = "Unable to open evaluation: " + error.message;
  }
}
function renderEmptyNetwork() {
  evaluation = {}; networkKey = "";
  $("network-notice").hidden = !bundle.report.reason;
  $("network-notice").textContent = bundle.report.reason || "";
  $("store-detail").textContent = "No store-to-site assignment recorded for the current report.";
  $("network-select").innerHTML = '<option>No saved evaluations</option>';
  $("site-filter").innerHTML = '<option value="">No open DCs</option>';
  $("network-stats").innerHTML = ""; renderMap(); renderSites(); setupDays();
}
function renderSites() {
  const e = evaluation, chosen = $("site-filter").value;
  $("sites-table").innerHTML = table(["Site", "Stores", "Bounds*", "Fleet", "Min service"], (e.sites || []).map(id => {
    const s = siteIndex.get(id), values = e.dc_days?.[id]?.service_before_noon_by_day || [];
    return `<tr class="${chosen === id ? "active" : ""}"><td><button class="cell-button" data-site="${esc(id)}"><span style="color:${siteColor(id)}">■</span> ${esc(id)}</button></td><td>${number(e.loads?.[id])}</td><td>${s ? number(s.minimum) + "–" + number(s.maximum) : "—"}</td><td>${number(e.fleet?.[id])}</td><td class="${values.length && Math.min(...values) < .98 - 1e-9 ? "bad" : ""}">${values.length ? percent(Math.min(...values)) : "—"}</td></tr>`;
  }));
}
function renderMap() {
  const e = evaluation || {}, assignments = e.store_to_site || {}, ids = Object.keys(assignments), selected = $("site-filter").value;
  const all = ids.map(id => storeIndex.get(id)).filter(Boolean), missing = ids.length - all.length;
  const points = [...all, ...bundle.geography.sites];
  const svg = $("network-map");
  if (!ids.length) {
    svg.innerHTML = '<text x="32" y="65" fill="#64748b" font-size="15">No saved feasible assignment to map for this evaluation.</text>';
    $("map-note").textContent = e.reason || "No store-to-site assignment recorded.";
    $("map-legend").innerHTML = ""; return;
  }
  const minLon = Math.min(...points.map(p => p.lon)), maxLon = Math.max(...points.map(p => p.lon));
  const minLat = Math.min(...points.map(p => p.lat)), maxLat = Math.max(...points.map(p => p.lat));
  const cos = Math.cos((minLat + maxLat) / 2 * Math.PI / 180), scale = Math.min(630 / ((maxLon - minLon) * cos || 1), 475 / (maxLat - minLat || 1));
  const x = lon => 350 + (lon - (minLon + maxLon) / 2) * cos * scale, y = lat => 285 - (lat - (minLat + maxLat) / 2) * scale;
  let content = '<rect x="-1000" y="-1000" width="3000" height="3000" fill="#f8fafc"/>';
  for (let lat = Math.ceil(minLat); lat <= maxLat; lat++) content += `<line class="map-grid" x1="20" x2="680" y1="${y(lat)}" y2="${y(lat)}"/><text x="22" y="${y(lat) - 5}" font-size="10" fill="#93a1b1">${lat}°N</text>`;
  for (let lon = Math.ceil(minLon); lon <= maxLon; lon++) content += `<line class="map-grid" x1="${x(lon)}" x2="${x(lon)}" y1="25" y2="535"/><text x="${x(lon) + 4}" y="533" font-size="10" fill="#93a1b1">${lon}°E</text>`;
  let shown = 0;
  for (const s of all) {
    const id = assignments[s.id];
    if (selected && id !== selected) continue;
    if ($("changed-only").checked && s.historical_site === id) continue;
    shown++;
    content += `<circle cx="${x(s.lon)}" cy="${y(s.lat)}" r="2.3" fill="${siteColor(id)}" opacity=".72" data-store="${esc(s.id)}"><title>${esc(s.id)} · ${esc(s.region)} → ${esc(id)}</title></circle>`;
  }
  for (const s of bundle.geography.sites) {
    const open = (e.sites || []).includes(s.id);
    if (!open && !$("show-closed").checked) continue;
    content += `<rect x="${x(s.lon) - 5}" y="${y(s.lat) - 5}" width="10" height="10" rx="1.5" fill="${open ? siteColor(s.id) : "#fff"}" stroke="${open ? "#fff" : "#9aa8ba"}" stroke-width="1.5" ${open ? `data-site="${esc(s.id)}" tabindex="0" role="button" aria-label="Highlight ${esc(s.id)}"` : ""}><title>${esc(s.id)} · ${esc(s.name)} · ${open ? "open" : "unused"}</title></rect>`;
    if (open && (!selected || selected === s.id)) content += `<text x="${x(s.lon) + 8}" y="${y(s.lat) - 6}" fill="#23374d" font-size="9" paint-order="stroke" stroke="#f8fafc" stroke-width="3">${esc(s.id)}</text>`;
  }
  svg.innerHTML = content; svg.setAttribute("viewBox", mapBox.join(" "));
  $("map-legend").innerHTML = (e.sites || []).map(id => `<span><i style="background:${siteColor(id)}"></i>${esc(id)}</span>`).join("");
  $("map-note").textContent = `${number(shown)} of ${number(ids.length)} assigned stores shown. Circles: stores. Squares: DC sites. Coordinate plot with no online tiles; links/road routes are not inferred. *Input store-count bounds; baseline assignments are exempt.${missing ? " " + missing + " store coordinates unavailable." : ""}`;
}
function chooseSite(id) {
  if (!(evaluation?.sites || []).includes(id)) return;
  $("site-filter").value = $("site-filter").value === id ? "" : id;
  renderMap(); renderSites();
}

function renderComparison() {
  const period = $("compare-set").value, holdout = period === "H14";
  const keys = holdout ? ["holdout:recommended", "holdout:baseline", "holdout:baseline_reassigned"] : ["recommended", "baseline", "reassigned"];
  const rows = keys.map(k => networkSummary(k));
  $("comparison-table").innerHTML = table(["Network", "Status", "DCs", "Owned", "Total · M KRW", "Mean service", "Min day service", "Reason"], rows.map((n, i) => `<tr><td>${n ? action(["Recommended", "Historical baseline", "Reassigned baseline"][i], keys[i]) : esc(["Recommended", "Historical baseline", "Reassigned baseline"][i])}</td><td>${statusPill(n)}</td><td>${number(n?.k)}</td><td>${number(fleet(n))}</td><td>${money(n?.total)}</td><td>${percent(n?.service_before_noon)}</td><td>${percent(n?.service_min_day)}</td><td class="wrap">${esc(n?.reason || (n ? "All saved service checks passed." : "Not evaluated in this run."))}</td></tr>`));
  const maximum = Math.max(1, ...rows.filter(Boolean).map(x => x.total || 0));
  $("cost-bars").innerHTML = rows.map((n, i) => `<div class="cost-row"><div class="cost-label"><span>${["Recommended", "Historical baseline", "Reassigned baseline"][i]}</span><b>${money(n?.total)}</b></div><div class="cost-track">${finite(n?.total) ? costParts.map(([key, label, color]) => `<span class="cost-segment" style="width:${100 * (n[key] || 0) / maximum}%;background:${color}" title="${esc(label)}: ${money(n[key])} KRW/month"></span>`).join("") : '<span class="note">No feasible assignment / no cost</span>'}</div></div>`).join("");
  $("cost-legend").innerHTML = costParts.map(([, label, color]) => `<span><i style="background:${color}"></i>${label}</span>`).join("");
  const saving = bundle.report[holdout ? "saving_H14" : "saving_D28"];
  $("savings-note").innerHTML = saving ? `<div class="saving"><span>Recorded savings against historical baseline · ${period}</span><strong>${number(saving.percent, 2)}%</strong><p>${money(saving.mean)} KRW/month · ${number(100 * saving.confidence)}% interval: ${money(saving.lower)}–${money(saving.upper)}</p><small>Paired common-day bootstrap, conditional on fitted models and fixed fleets. The historical baseline fails the service target; savings include avoided failure penalties.${holdout ? " H14 uses the D28 fleets without resizing." : ""}</small></div>` : '<div class="notice neutral">No saved paired savings estimate.</div>';
  const avg = networkSummary("average"), rec = networkSummary("recommended");
  $("average-note").textContent = avg && rec ? `Mean-demand sensitivity: ${money(avg.total)} versus ${money(rec.total)} KRW/month on D28; the mean-day estimate is lower by ${money(bundle.report.average_day_gap)} (${percent(bundle.report.average_day_gap / rec.total)}). Owned fleets: ${number(fleet(avg))} and ${number(fleet(rec))}, respectively. It is a sensitivity, not the final selection criterion.` : "No mean-demand sensitivity is saved for this run.";
}

function setupDays() {
  const e = evaluation || {}, days = e.day_labels || [];
  $("day-context").textContent = `${networkSummary(networkKey)?.label || "Selected evaluation"} · ${e.fleet_source || "No saved fleet"}. Day-level cost is a monthly equivalent, not a daily invoice. Day weights enter reported aggregate costs and service.`;
  $("day-select").innerHTML = days.length ? days.map((d, i) => `<option value="${i}">${i + 1}. ${esc(d)} · weight ${percent(e.weights?.[i])}</option>`).join("") : '<option value="0">No saved sampled days</option>';
  $("day-select").disabled = !days.length;
  $("day-site").innerHTML = '<option value="">Whole network</option>' + Object.keys(e.dc_days || {}).map(s => `<option>${esc(s)}</option>`).join("");
  $("day-slider").max = Math.max(0, days.length - 1); $("day-slider").value = 0; $("day-slider").disabled = !days.length;
  renderDay();
}
function renderDay() {
  const e = evaluation || {}, index = Number($("day-select").value || 0), site = $("day-site").value;
  const row = site ? e.dc_days?.[site] : e, days = e.day_labels || [];
  if (!days.length || !row) { $("day-stats").innerHTML = kpi("—", "No saved day-level result"); $("day-table").innerHTML = ""; $("day-chart").innerHTML = ""; return; }
  const service = row.service_before_noon_by_day?.[index];
  const needed = site ? row.vehicles_needed?.[index] : sum(Object.values(e.dc_days || {}).map(x => x.vehicles_needed?.[index]));
  const spot = site ? row.spot_by_day?.[index] : sum(Object.values(e.dc_days || {}).map(x => x.spot_by_day?.[index]));
  const unserved = site ? row.unserved_by_day?.[index]?.length : e.unserved_by_day?.[index];
  $("day-stats").innerHTML = kpi(percent(service), "Before-noon service", service < .98 - 1e-9 ? "Below 98% target" : "Meets 98% target") + kpi(number(needed), "Vehicles needed", number(spot) + " spot vehicles") + kpi(number(unserved), "Undelivered stores", site || "Whole network") + kpi(site ? number(row.fleet) : money(e.per_day_total?.[index]), site ? "Owned vehicles at this DC" : "Monthly-equivalent cost · M KRW", days[index]);
  const ids = site ? [site] : Object.keys(e.dc_days || {});
  $("day-table").innerHTML = table(["DC", "Owned", "Needed", "Spot", "Service", "Undelivered"], ids.map(id => {
    const d = e.dc_days[id], s = d.service_before_noon_by_day?.[index];
    return `<tr><td>${esc(id)}</td><td>${number(d.fleet)}</td><td>${number(d.vehicles_needed?.[index])}</td><td>${number(d.spot_by_day?.[index])}</td><td class="${s < .98 - 1e-9 ? "bad" : "good"}">${percent(s)}</td><td>${number(d.unserved_by_day?.[index]?.length)}</td></tr>`;
  }));
  const values = row.service_before_noon_by_day || [];
  drawChart("day-chart", [{color: "#107c75", points: values.map((v, i) => ({x: i + 1, y: v * 100, id: i, label: `${days[i]} · ${percent(v)}`, selected: i === index}))}], {xLabel: "Saved day index", yLabel: "Before-noon service (%)", target: 98, yMin: Math.min(97.5, ...values.map(v => v * 100 - .4)), yMax: 100.5, onClick: i => { $("day-select").value = i; $("day-slider").value = i; renderDay(); }});
}

function renderSweep() {
  const rows = bundle.report.sweep || [];
  $("sweep-table").innerHTML = table(["DCs", "Best E · M", "Seed status", "D28 result"], rows.map(row => {
    const cert = row.seed_certificate?.status;
    return `<tr><td>${row.best_candidate_id ? action("k = " + row.k, "final:" + row.best_candidate_id) : "k = " + row.k}</td><td>${money(row.best_E)}</td><td><span class="status-pill ${cert === "infeasible" ? "fail" : cert === "unknown" ? "unknown" : ""}">${esc(cert || "—")}</span></td><td class="wrap">${esc(row.status)}</td></tr>`;
  }));
  drawChart("sweep-chart", [{color: "#107c75", points: rows.filter(x => finite(x.best_E)).map(x => ({x: x.k, y: x.best_E / 1e6, id: "final:" + x.best_candidate_id, label: `k=${x.k} · ${money(x.best_E)} KRW/month`}))}], {xLabel: "Number of open DCs", yLabel: "Best feasible E (M KRW / month)", xMin: rows.length ? Math.min(...rows.map(r => r.k)) : undefined, xMax: rows.length ? Math.max(...rows.map(r => r.k)) : undefined, onClick: key => selectNetwork(key)});
}
function searchSeries(rule) {
  let best = null;
  return (bundle.searches[rule] || []).map(row => { if (row.feasible && finite(row.total)) best = best === null ? row.total : Math.min(best, row.total); return {x: row.iteration, y: best === null ? null : best / 1e6, label: `${rule} #${row.iteration} · best ${money(best)}`}; });
}
function renderSearch() {
  const guided = bundle.searches.guided || [], random = bundle.searches.random || [];
  const stats = rows => `${rows.length} evaluations · ${rows.filter(x => x.feasible).length} service-feasible · ${number(sum(rows.map(x => x.lower_level_solves)))} DC-day solves · ${number(sum(rows.map(x => x.seconds)), 1)} s`;
  $("search-facts").innerHTML = `<div class="fact"><strong>Guided search</strong><p>${esc(stats(guided))}</p></div><div class="fact"><strong>Random control</strong><p>${esc(stats(random))}</p></div><div class="fact"><strong>Equal evaluation budgets, different work</strong><p>These are two saved runs, not repeated-trial averages. Runtime and solve counts need not match. Only service-feasible results update the incumbent.</p></div>`;
  const budget = Math.max(1, guided.length, random.length);
  $("budget").max = budget; $("budget").value = budget;
  renderSearchCharts(); renderLog();
}
function renderSearchCharts() {
  const rule = $("search-rule").value, rows = bundle.searches[rule] || [], budget = Number($("budget").value);
  $("budget-label").textContent = budget;
  drawChart("scatter-chart", [{color: "#107c75", line: false, points: rows.filter(x => finite(x.G) && finite(x.total)).map(x => ({x: x.G / 1e6, y: x.total / 1e6, color: x.feasible ? "#107c75" : "#d77770", id: x.id, label: `${rule} #${x.iteration} · k=${x.k} · G=${money(x.G)}, E=${money(x.total)} · ${x.feasible ? "service-feasible" : "service failure"}`}))}], {xLabel: "Assignment proxy G (M KRW)", yLabel: "Full routing E (M KRW / month)", onClick: key => selectNetwork(key)});
  const series = [{color: "#107c75", points: searchSeries("guided")}, {color: "#6c67d9", points: searchSeries("random")}];
  drawChart("curve-chart", series, {xLabel: "Full evaluations", yLabel: "Best feasible E (M KRW / month)", cursor: budget});
  const best = ruleName => { const p = searchSeries(ruleName).filter(x => x.x <= budget && finite(x.y)).at(-1); return p ? number(p.y, 1) + " M" : "no feasible incumbent"; };
  $("budget-note").textContent = `At ${budget} evaluations: guided ${best("guided")}, random ${best("random")} KRW/month. The shared seed phase is included in both budgets.`;
}
function renderLog() {
  let rows = [...(bundle.searches[$("search-rule").value] || [])];
  const query = $("log-filter").value.trim().toLowerCase();
  if (query) rows = rows.filter(x => `${x.candidate_id} ${x.phase} ${x.status} ${x.reason || ""} ${x.feasible ? "feasible" : "infeasible"}`.toLowerCase().includes(query));
  if ($("log-order").value === "cost") rows.sort((a, b) => (a.total ?? Infinity) - (b.total ?? Infinity));
  $("log-table").innerHTML = table(["# / candidate", "k", "Phase", "G · M", "E · M", "Service", "Solves", "Seconds"], rows.map(x => `<tr><td title="${esc(x.candidate_id)}">${action("#" + x.iteration + " · " + x.candidate_id.replaceAll("SITE_", "").split("+").join(" / "), x.id)}</td><td>${number(x.k)}</td><td>${esc(x.phase)}</td><td>${money(x.G)}</td><td>${money(x.total)}</td><td>${statusPill(x)}</td><td>${number(x.lower_level_solves)}</td><td>${number(x.seconds, 1)}</td></tr>`));
}

// Small SVG charts adapted from the toy viewer: local rendering, no chart CDN.
function drawChart(id, series, options = {}) {
  const svg = $(id), W = 620, H = Number(svg.viewBox.baseVal.height) || 330, m = {l: 66, r: 18, t: 28, b: 48};
  const points = series.flatMap(s => s.points).filter(p => finite(p.x) && finite(p.y));
  if (!points.length) { svg.innerHTML = '<text x="30" y="50">No saved numeric observations for this view.</text>'; return; }
  let x0 = options.xMin ?? Math.min(...points.map(p => p.x)), x1 = options.xMax ?? Math.max(...points.map(p => p.x));
  let y0 = options.yMin ?? Math.min(...points.map(p => p.y)), y1 = options.yMax ?? Math.max(...points.map(p => p.y));
  if (x0 === x1) { x0 -= .5; x1 += .5; }
  if (y0 === y1) { y0 -= Math.max(1, Math.abs(y0) * .01); y1 += Math.max(1, Math.abs(y1) * .01); }
  if (options.yMin == null) { const padding = (y1 - y0) * .1; y0 -= padding; y1 += padding; }
  const X = x => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r), Y = y => H - m.b - (y - y0) / (y1 - y0) * (H - m.t - m.b);
  let markup = "";
  for (let i = 0; i <= 4; i++) {
    const y = y0 + (y1 - y0) * i / 4, x = x0 + (x1 - x0) * i / 4;
    markup += `<line class="gridline" x1="${m.l}" x2="${W-m.r}" y1="${Y(y)}" y2="${Y(y)}"/><text x="${m.l-8}" y="${Y(y)+3}" text-anchor="end">${number(y, y1 - y0 < 4 ? 1 : 0)}</text><text x="${X(x)}" y="${H-m.b+18}" text-anchor="middle">${number(x, x1 - x0 < 4 ? 1 : 0)}</text>`;
  }
  markup += `<text x="${m.l}" y="14">${esc(options.yLabel || "")}</text><text x="${(m.l + W - m.r) / 2}" y="${H-5}" text-anchor="middle">${esc(options.xLabel || "")}</text>`;
  if (finite(options.target) && options.target >= y0 && options.target <= y1) markup += `<line x1="${m.l}" x2="${W-m.r}" y1="${Y(options.target)}" y2="${Y(options.target)}" stroke="#bd4c47" stroke-dasharray="5 4"/><text x="${W-m.r}" y="${Y(options.target)-5}" text-anchor="end">98% target</text>`;
  if (finite(options.cursor)) markup += `<line x1="${X(options.cursor)}" x2="${X(options.cursor)}" y1="${m.t}" y2="${H-m.b}" stroke="#9aa8ba" stroke-dasharray="3 4"/>`;
  for (const s of series) {
    const valid = s.points.filter(p => finite(p.x) && finite(p.y));
    if (s.line !== false && valid.length > 1) markup += `<polyline points="${valid.map(p => X(p.x) + "," + Y(p.y)).join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
    for (const p of valid) markup += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.selected ? 6 : 3.6}" fill="${p.color || s.color}" stroke="white" stroke-width="1" ${options.onClick && p.id !== undefined ? `data-point="${esc(p.id)}" role="button" tabindex="0" aria-label="${esc(p.label || "Inspect point")}"` : ""}><title>${esc(p.label || `${number(p.x, 1)}, ${number(p.y, 1)}`)}</title></circle>`;
  }
  svg.innerHTML = markup;
  svg.onclick = event => { const key = event.target.dataset.point; if (key !== undefined && options.onClick) options.onClick(key); };
  svg.onkeydown = event => { if (["Enter", " "].includes(event.key) && event.target.dataset.point !== undefined) { event.preventDefault(); event.target.click(); } };
}

function renderSources() {
  $("source-count").textContent = bundle.files.length + " saved artifacts";
  $("source-select").innerHTML = bundle.files.map(f => `<option value="${esc(f.name)}">${esc(f.name)} · ${number(f.bytes / 1024, 1)} KB</option>`).join("");
  $("source-select").value = "report.json"; updateSourceLink();
  $("method-note").textContent = `G uses the assignment proxy; E includes DC fixed cost, owned vehicles, fuel, spot and failure costs. Lambda = ${number(bundle.report.lambda, 2)}. ${bundle.report.lambda_definition || ""} Uncertainty intervals are conditional empirical paired-day bootstraps, not global model uncertainty. The saved report is authoritative if older descriptive documents disagree.`;
  rawSource = ""; $("source-json").textContent = 'Select an artifact and choose “View source”.';
}
function updateSourceLink() {
  ++sourceRequest; rawSource = "";
  $("download-source").href = "/api/result?file=" + encodeURIComponent($("source-select").value) + "&download=1";
  $("source-note").textContent = "Download returns the original saved file; no data is regenerated.";
  $("source-json").textContent = 'Choose “View source” to read this artifact.';
}
async function viewSource() {
  const token = ++sourceRequest;
  const name = $("source-select").value;
  $("source-note").textContent = "Loading " + name + "…";
  try {
    const response = await fetch("/api/result?file=" + encodeURIComponent(name), {cache: "no-store"});
    if (!response.ok) throw Error("HTTP " + response.status);
    const text = await response.text();
    if (token !== sourceRequest) return;
    rawSource = text;
    const limit = 100000;
    $("source-json").textContent = text.slice(0, limit) + (text.length > limit ? "\n… Preview limited to 100,000 characters. Download or copy for the complete original." : "");
    $("source-note").textContent = name + " · " + number(text.length) + " characters · read from disk";
  } catch (error) { if (token === sourceRequest) $("source-note").textContent = "Unable to read source: " + error.message; }
}

$("refresh").onclick = loadDashboard;
$("network-select").onchange = event => selectNetwork(event.target.value, false);
$("site-filter").onchange = () => { renderMap(); renderSites(); };
$("show-closed").onchange = renderMap; $("changed-only").onchange = renderMap;
$("compare-set").onchange = renderComparison;
$("day-select").onchange = () => { $("day-slider").value = $("day-select").value; renderDay(); };
$("day-slider").oninput = () => { $("day-select").value = $("day-slider").value; renderDay(); };
$("day-site").onchange = renderDay;
$("search-rule").onchange = () => { renderSearchCharts(); renderLog(); };
$("budget").oninput = renderSearchCharts; $("log-filter").oninput = renderLog; $("log-order").onchange = renderLog;
$("source-select").onchange = updateSourceLink; $("view-source").onclick = viewSource;
$("copy-source").onclick = async () => {
  if (!rawSource) await viewSource();
  if (!rawSource) return;
  try { await navigator.clipboard.writeText(rawSource); $("source-note").textContent = "Complete source copied."; }
  catch (_) { $("source-note").textContent = "Clipboard unavailable. Use Download original."; }
};
document.addEventListener("click", event => {
  const link = event.target.closest("[data-network]");
  if (link) selectNetwork(link.dataset.network);
  const site = event.target.closest("[data-site]");
  if (site) chooseSite(site.dataset.site);
});
$("network-map").addEventListener("keydown", event => {
  if (["Enter", " "].includes(event.key) && event.target.dataset.site) { event.preventDefault(); chooseSite(event.target.dataset.site); }
});
$("network-map").addEventListener("pointerover", event => {
  const s = storeIndex.get(event.target.dataset.store);
  if (s) $("store-detail").textContent = `${s.id} · ${s.region} · ${s.type} → ${evaluation.store_to_site[s.id]} (historical: ${s.historical_site || "unknown"})`;
});
function zoomMap(factor, fx = .5, fy = .5) {
  const [x, y, w, h] = mapBox, next = Math.min(1400, Math.max(80, w * factor)), ratio = next / w;
  mapBox = [x + w * fx * (1 - ratio), y + h * fy * (1 - ratio), next, h * ratio];
  $("network-map").setAttribute("viewBox", mapBox.join(" "));
}
$("zoom-in").onclick = () => zoomMap(.8); $("zoom-out").onclick = () => zoomMap(1.25);
$("reset-map").onclick = () => { mapBox = [0, 0, 700, 560]; $("network-map").setAttribute("viewBox", mapBox.join(" ")); };
$("network-map").addEventListener("wheel", event => {
  event.preventDefault(); const rect = $("network-map").getBoundingClientRect();
  zoomMap(event.deltaY > 0 ? 1.12 : .89, (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
}, {passive: false});
let drag = null;
$("network-map").addEventListener("pointerdown", event => {
  if (event.button !== 0 || event.target.dataset.site) return;
  drag = {x: event.clientX, y: event.clientY, box: [...mapBox]}; $("network-map").setPointerCapture(event.pointerId);
});
$("network-map").addEventListener("pointermove", event => {
  if (!drag) return;
  const rect = $("network-map").getBoundingClientRect();
  mapBox = [drag.box[0] - (event.clientX - drag.x) / rect.width * drag.box[2], drag.box[1] - (event.clientY - drag.y) / rect.height * drag.box[3], drag.box[2], drag.box[3]];
  $("network-map").setAttribute("viewBox", mapBox.join(" "));
});
$("network-map").addEventListener("pointerup", () => drag = null);
$("network-map").addEventListener("pointercancel", () => drag = null);

if (location.protocol === "file:") showStatus("Open this presentation via run_demo.py on localhost. Browsers cannot load the saved result API from a file:// URL.", true);
else loadDashboard();
