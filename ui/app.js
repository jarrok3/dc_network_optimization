if (typeof TOY !== "undefined" && TOY) BASE_PARAMS = Object.assign({}, BASE_PARAMS, TOY.params);
const SVGNS = "http://www.w3.org/2000/svg";
function addKids(e, kids){ for (const k of kids.flat()){ if (k === null || k === undefined || k === false) continue; e.appendChild(typeof k === "object" ? k : document.createTextNode(String(k))); } }
function setAttrs(e, a){
  for (const k in (a || {})){
    const v = a[k]; if (v === null || v === undefined || v === false) continue;
    if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v); else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
}
function h(tag, attrs, ...kids){ const e = document.createElement(tag); setAttrs(e, attrs); addKids(e, kids); return e; }
function sv(tag, attrs, ...kids){ const e = document.createElementNS(SVGNS, tag); setAttrs(e, attrs); addKids(e, kids); return e; }
function clear(e){ while (e.firstChild) e.removeChild(e.firstChild); return e; }
const $ = id => document.getElementById(id);
const fM = x => (x / 1e6).toFixed(1), fM2 = x => (x / 1e6).toFixed(2), fP = (x, d) => (x).toFixed(d === undefined ? 1 : d) + "%";
const fK = x => Math.round(x).toLocaleString("en-US");
const SITE_COL = ["#2a6fdb", "#e0702b", "#2f9e5b", "#a24fd1"];
const ROUTE_COL = ["#2a6fdb", "#e0702b", "#2f9e5b", "#a24fd1", "#c49a00", "#d1457a", "#0e9aa7", "#7a5af8", "#6c8a1f", "#c0392b"];
const R = (typeof TOY !== "undefined" && TOY) ? TOY.results : null;
const siteIndex = id => TOY.sites.findIndex(s => s.id === id);
const siteById = id => TOY.sites[siteIndex(id)];

let DIST = null, DIDX = null;
function buildDist(){
  const ids = TOY.dist.ids, n = ids.length; DIDX = {}; ids.forEach((v, i) => DIDX[v] = i);
  DIST = new Float64Array(n * n); let k = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++){ const v = TOY.dist.tri[k++]; DIST[i * n + j] = v; DIST[j * n + i] = v; }
}
function subMatrix(ids){
  const n = TOY.dist.ids.length, ix = ids.map(i => DIDX[i]);
  return ix.map(a => ix.map(b => DIST[a * n + b]));
}

const NETS = [];
function buildNets(){
  R.final.forEach(r => NETS.push({key: "final:" + r.role, label: r.label, sites: r.sites, assign: r.assign, fleet: r.final.fleet, ev: r.final, group: "Reference networks"}));
  R.candidates.filter(c => c.feasible && c.eval && c.eval.status === "ok").sort((a, b2) => a.G - b2.G).forEach(c =>
    NETS.push({key: "cand:" + c.id, label: "Candidate " + c.id + " · k = " + c.k + " · G " + fM(c.G) + " M · E " + fM(c.E) + " M", sites: c.sites, assign: c.assign, fleet: c.eval.fleet, ev: c.eval, group: "Feasible candidates (ascending G)"}));
}
function fillSelect(sel, nets, keep){
  clear(sel); const groups = {};
  nets.forEach(n => { (groups[n.group] = groups[n.group] || []).push(n); });
  Object.keys(groups).forEach(g => { const og = h("optgroup", {label: g}); groups[g].forEach(n => og.appendChild(h("option", {value: n.key}, n.label))); sel.appendChild(og); });
  if (keep) sel.value = keep;
}

function projector(W, H, pad){
  const g = TOY.geo, lat0 = (g.lat_min + g.lat_max) / 2, kx = Math.cos(lat0 * Math.PI / 180);
  const wKm = (g.lon_max - g.lon_min) * 111 * kx, hKm = (g.lat_max - g.lat_min) * 111;
  const sc = Math.min((W - 2 * pad) / wKm, (H - 2 * pad) / hKm);
  return (lat, lon) => [pad + (lon - g.lon_min) * 111 * kx * sc, pad + (g.lat_max - lat) * 111 * sc];
}
function mapBase(svg, pr){
  clear(svg);
  svg.appendChild(sv("rect", {x: 0, y: 0, width: 640, height: 600, fill: "none"}));
  const r = TOY.geo.river, a = pr(r[0][0], r[0][1]), b = pr(r[1][0], r[1][1]);
  svg.appendChild(sv("line", {x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: "var(--river)", "stroke-width": 7, "stroke-linecap": "round", opacity: 0.6}));
  TOY.geo.bridges.forEach(br => { const p = pr(br[0], br[1]); svg.appendChild(sv("rect", {x: p[0] - 6, y: p[1] - 3, width: 12, height: 6, fill: "var(--mute)", opacity: 0.7})); });
  svg.appendChild(sv("text", {x: a[0] + 6, y: a[1] - 10, fill: "var(--river)", "font-size": 12}, "river (cross only at bridges)"));
}
function drawNetwork(svg, net){
  const pr = projector(640, 600, 30); mapBase(svg, pr);
  const open = new Set(net.sites);
  TOY.stores.forEach((s, i) => { const p = pr(s.lat, s.lon); svg.appendChild(sv("circle", {cx: p[0], cy: p[1], r: 3, fill: SITE_COL[siteIndex(net.sites[net.assign[i]]) % SITE_COL.length], opacity: 0.85}, sv("title", {}, s.id + " · " + s.type + " → " + net.sites[net.assign[i]]))); });
  TOY.sites.forEach((s, k) => {
    const p = pr(s.lat, s.lon);
    if (open.has(s.id)) svg.appendChild(sv("rect", {x: p[0] - 8, y: p[1] - 8, width: 16, height: 16, fill: SITE_COL[k], stroke: "var(--ink)", "stroke-width": 2}));
    else svg.appendChild(sv("rect", {x: p[0] - 6, y: p[1] - 6, width: 12, height: 12, fill: "none", stroke: "var(--mute)", "stroke-width": 1.5, transform: "rotate(45 " + p[0] + " " + p[1] + ")"}));
    svg.appendChild(sv("text", {x: p[0] + 11, y: p[1] - 9, fill: "var(--ink)", "font-size": 13, "font-weight": 600}, s.id));
  });
}
function renderMap(key){
  const net = NETS.find(n => n.key === key) || NETS[0];
  drawNetwork($("mapsvg"), net);
  const lg = clear($("maplegend"));
  TOY.sites.forEach((s, k) => lg.appendChild(h("span", {}, h("i", {style: "background:" + SITE_COL[k]}), s.id + (net.sites.indexOf(s.id) >= 0 ? " (open)" : " (closed)"))));
  const info = clear($("mapinfo"));
  info.appendChild(h("h3", {}, net.label));
  const rows = net.sites.map((sid, j) => { const n = net.assign.filter(a => a === j).length, s = siteById(sid); return h("tr", {}, h("td", {}, sid + " · " + s.name), h("td", {}, n), h("td", {}, TOY.meta.min_stores + " – " + s.max), h("td", {class: (n < TOY.meta.min_stores || n > s.max) ? "neg" : ""}, (n < TOY.meta.min_stores || n > s.max) ? "outside bounds (baseline exempt)" : "within bounds"), h("td", {}, fM(s.fixed) + " M")); });
  info.appendChild(h("table", {}, h("tr", {}, h("th", {class: "l"}, "Open DC"), h("th", {}, "Stores"), h("th", {}, "Allowed"), h("th", {}, "Bounds"), h("th", {}, "Fixed / month")), rows));
  if (net.ev && net.ev.status === "ok"){
    const e = net.ev;
    info.appendChild(h("h3", {}, "Monthly cost"));
    info.appendChild(h("table", {}, [["DC fixed", e.dc_fixed], ["Vehicles", e.vehicle_fixed], ["Fuel", e.fuel], ["Spot trucks", e.spot], ["Failures", e.failure], ["Total", e.total]].map(r => h("tr", {}, h("td", {}, r[0]), h("td", {}, fM(r[1]) + " M KRW")))));
    info.appendChild(h("p", {class: "note"}, "Share of deliveries before 12:00: " + fP(100 * e.service_before_noon) + " (worst day " + fP(100 * e.service_min_day) + ")."));
  }
}

function ticks(lo, hi, n){
  const span = hi - lo, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw || 1))), f = raw / mag;
  const step = (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
function axes(svg, W, H, m, xr, yr, xl, yl, xt, yt){
  const X = v => m.l + (v - xr[0]) / (xr[1] - xr[0]) * (W - m.l - m.r), Y = v => H - m.b - (v - yr[0]) / (yr[1] - yr[0]) * (H - m.t - m.b);
  ticks(yr[0], yr[1], yt || 5).forEach(v => { svg.appendChild(sv("line", {x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: "var(--grid)"})); svg.appendChild(sv("text", {x: m.l - 6, y: Y(v) + 4, "text-anchor": "end"}, +v.toFixed(2))); });
  if (xt !== false) ticks(xr[0], xr[1], xt || 5).forEach(v => svg.appendChild(sv("text", {x: X(v), y: H - m.b + 14, "text-anchor": "middle"}, +v.toFixed(2))));
  svg.appendChild(sv("line", {x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: "ax"}));
  svg.appendChild(sv("text", {x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle"}, xl));
  svg.appendChild(sv("text", {x: 12, y: (m.t + H - m.b) / 2, "text-anchor": "middle", transform: "rotate(-90 12 " + ((m.t + H - m.b) / 2) + ")"}, yl));
  return {X: X, Y: Y};
}

function renderSweep(){
  const svg = clear($("sweepsvg")), W = 520, H = 330, m = {l: 48, r: 12, t: 16, b: 46};
  const sw = R.sweep, comps = [["dc_fixed", "DC fixed", "#2a6fdb"], ["vehicle_fixed", "Vehicles", "#e0702b"], ["fuel", "Fuel", "#2f9e5b"], ["sf", "Spot + failures", "#c0392b"]];
  const ok = sw.filter(s => s.feasible);
  const maxV = (ok.length ? Math.max(...ok.map(s => s.e_best_eval.total)) : 1) / 1e6 * 1.12;
  const ax = axes(svg, W, H, m, [0.5, sw.length + 0.5], [0, maxV], "Number of open DCs k", "M KRW / month", false);
  const bw = (W - m.l - m.r) / sw.length * 0.55;
  sw.forEach(s => {
    const cx = ax.X(s.k); svg.appendChild(sv("text", {x: cx, y: H - m.b + 14, "text-anchor": "middle"}, "k = " + s.k));
    if (!s.feasible){
      svg.appendChild(sv("rect", {x: cx - bw / 2, y: ax.Y(maxV * 0.35), width: bw, height: ax.Y(0) - ax.Y(maxV * 0.35), fill: "none", stroke: "var(--bad)", "stroke-dasharray": "4 3"}));
      svg.appendChild(sv("text", {x: cx, y: ax.Y(maxV * 0.18), "text-anchor": "middle", fill: "var(--bad)"}, "infeasible"));
      return;
    }
    const e = s.e_best_eval; let y0 = 0;
    comps.forEach(c => {
      const v = c[0] === "sf" ? (e.spot + e.failure) / 1e6 : e[c[0]] / 1e6;
      svg.appendChild(sv("rect", {x: cx - bw / 2, y: ax.Y(y0 + v), width: bw, height: ax.Y(y0) - ax.Y(y0 + v), fill: c[2]}, sv("title", {}, c[1] + ": " + v.toFixed(1) + " M")));
      y0 += v;
    });
    svg.appendChild(sv("text", {x: cx, y: ax.Y(y0) - 5, "text-anchor": "middle", style: "fill:var(--ink);font-weight:600"}, y0.toFixed(1)));
  });
  const t = clear($("sweeptable"));
  t.appendChild(h("table", {}, h("tr", {}, h("th", {}, "k"), h("th", {class: "l"}, "Best by E"), h("th", {}, "E (M)"), h("th", {class: "l"}, "G-best"), h("th", {}, "its E (M)")),
    sw.map(s => s.feasible ? h("tr", {}, h("td", {}, s.k), h("td", {class: "l"}, s.e_best), h("td", {}, fM(s.e_best_eval.total)), h("td", {class: "l"}, s.g_best), h("td", {}, fM(s.g_best_E)))
      : h("tr", {}, h("td", {}, s.k), h("td", {class: "l neg", colspan: 4}, s.reason)))));
}

function renderScatter(){
  const svg = clear($("scatter")), W = 520, H = 380, m = {l: 50, r: 14, t: 14, b: 44};
  const cs = R.candidates.filter(c => c.feasible && c.E != null), g = cs.map(c => c.G / 1e6), e = cs.map(c => c.E / 1e6);
  if (!cs.length) return;
  const lo = Math.min(...g, ...e) * 0.98, hi = Math.max(...g, ...e) * 1.02;
  const ax = axes(svg, W, H, m, [lo, hi], [lo, hi], "G (M KRW / month)", "E (M KRW / month)");
  svg.appendChild(sv("line", {x1: ax.X(lo), y1: ax.Y(lo), x2: ax.X(hi), y2: ax.Y(hi), stroke: "var(--mute)", "stroke-dasharray": "4 4"}));
  const ks = [...new Set(cs.map(c => c.k))].sort(), col = k => SITE_COL[ks.indexOf(k) % 4];
  cs.forEach(c => svg.appendChild(sv("circle", {cx: ax.X(c.G / 1e6), cy: ax.Y(c.E / 1e6), r: 5, fill: col(c.k), opacity: 0.85}, sv("title", {}, c.id + " · G " + fM(c.G) + " · E " + fM(c.E)))));
  const mt = R.metrics;
  [[mt.g_best, "G-best", "var(--ink)"], [mt.e_best, "E-best", "var(--bad)"]].forEach((o, i) => {
    const c = cs.find(x => x.id === o[0]); if (!c) return;
    svg.appendChild(sv("circle", {cx: ax.X(c.G / 1e6), cy: ax.Y(c.E / 1e6), r: 9 + 3 * i, fill: "none", stroke: o[2], "stroke-width": 2}));
  });
  const lg = clear($("scatterlegend"));
  ks.forEach(k => lg.appendChild(h("span", {}, h("i", {style: "background:" + col(k)}), "k = " + k)));
  const box = clear($("gvemetrics"));
  box.appendChild(h("div", {class: "grid g2"},
    h("div", {class: "kpi"}, h("b", {}, mt.spearman.toFixed(2)), h("span", {}, "Spearman rank correlation G–E (prediction quality)")),
    h("div", {class: "kpi"}, h("b", {}, fP(mt.decision_loss_pct, 2)), h("span", {}, "loss of the G-best candidate under E (decision quality)")),
    h("div", {class: "kpi"}, h("b", {}, "#" + mt.g_best_rank_under_E), h("span", {}, "rank of the G-best candidate under E")),
    h("div", {class: "kpi"}, h("b", {}, mt.ratio_E_over_G.min.toFixed(2) + " – " + mt.ratio_E_over_G.max.toFixed(2)), h("span", {}, "E / G range"))));
}

function renderCurve(){
  const svg = clear($("curve")), W = 520, H = 300, m = {l: 50, r: 14, t: 14, b: 40}, c = R.curves;
  if (!c.n.length) return;
  const lo = c.optimum / 1e6 * 0.99, hi = Math.max(...c.random_p90, ...c.g_order) / 1e6 * 1.02, n = c.n.length;
  const ax = axes(svg, W, H, m, [1, n], [lo, hi], "Evaluations made", "Best E found (M KRW)");
  const pts = a => a.map((v, i) => ax.X(i + 1) + "," + ax.Y(v / 1e6)).join(" ");
  const band = c.random_p90.map((v, i) => ax.X(i + 1) + "," + ax.Y(v / 1e6)).concat(c.random_p10.map((v, i) => [ax.X(i + 1), ax.Y(v / 1e6)]).reverse().map(p => p.join(",")));
  svg.appendChild(sv("polygon", {points: band.join(" "), fill: "var(--acc)", opacity: 0.15}));
  svg.appendChild(sv("polyline", {points: pts(c.random_mean), fill: "none", stroke: "var(--mute)", "stroke-width": 2, "stroke-dasharray": "5 3"}));
  svg.appendChild(sv("polyline", {points: pts(c.g_order), fill: "none", stroke: "var(--acc)", "stroke-width": 2.5}));
  $("curvenote").textContent = "Solid: visit in ascending G. Dashed: random order (mean, 10–90% band). Toy scale evaluates the whole feasible pool; this is the analogue of comparing a selection rule with random selection at the same budget.";
}

function renderLog(){
  const t = clear($("logtable"));
  t.appendChild(h("table", {}, h("tr", {}, h("th", {}, "#"), h("th", {class: "l"}, "Candidate"), h("th", {}, "k"), h("th", {}, "G (M)"), h("th", {}, "E (M)"), h("th", {}, "Best so far"), h("th", {class: "l"}, "Rule")),
    R.log.map(r => h("tr", {class: "click", onclick: () => { if (r.E == null) return; $("mapsel").value = "cand:" + r.candidate; renderMap("cand:" + r.candidate); $("map").scrollIntoView(); }},
      h("td", {}, r.n), h("td", {class: "l"}, r.candidate), h("td", {}, r.k), h("td", {}, fM(r.G)), h("td", {}, r.E == null ? "—" : fM(r.E)), h("td", {}, r.best_so_far == null ? "—" : fM(r.best_so_far)), h("td", {class: "l"}, r.rule)))));
}

function renderCompare(){
  const t = clear($("comparetable"));
  const sv_ = (o) => o ? h("span", {class: o.mean > 0 ? "pos" : "neg"}, fM(o.mean) + " M (" + fP(o.pct) + ")") : "—";
  t.appendChild(h("table", {}, h("tr", {}, h("th", {class: "l"}, "Network"), h("th", {}, "k"), h("th", {}, "DC fixed"), h("th", {}, "Vehicles"), h("th", {}, "Fuel"), h("th", {}, "Spot"), h("th", {}, "Total (final)"), h("th", {}, "Before noon"), h("th", {}, "Saving vs baseline"), h("th", {}, "Held-out")),
    R.final.map(r => { const f = r.final; return h("tr", {}, h("td", {class: "l"}, r.label), h("td", {}, r.k), h("td", {}, fM(f.dc_fixed)), h("td", {}, fM(f.vehicle_fixed)), h("td", {}, fM(f.fuel)), h("td", {}, fM(f.spot)), h("td", {}, h("b", {}, fM(f.total))), h("td", {}, fP(100 * f.service_before_noon)), h("td", {}, sv_(r.saving_final)), h("td", {}, fM(r.holdout.total))); })));
  const a = R.average_day, box = clear($("avgday"));
  box.appendChild(h("h3", {}, "Sampled-day versus average-day evaluation"));
  box.appendChild(h("p", {}, "For ", h("b", {}, a.network), ": sampled-day cost ", h("b", {}, fM(a.sampled_day_total) + " M"), " versus ", h("b", {}, fM(a.average_day_total) + " M"), " on one average day (gap ", h("b", {}, fP(a.gap_pct)), "). Fleet size and spot hiring are not linear in demand."));
}

function dayOptions(){
  const out = [];
  ["search", "final", "holdout"].forEach(set => TOY.days[set].dates.forEach((d, i) => out.push({key: set + ":" + i, label: set + " · " + d + " · " + TOY.days[set].strata[i], demand: TOY.days[set].demand[i]})));
  out.push({key: "avg", label: "average day of the final set", demand: TOY.average_final});
  return out;
}
function liveNets(){ return NETS.filter(n => n.fleet); }
function field(label, el){ return h("div", {}, h("label", {}, label), el); }
function busiestSite(key){
  const n = liveNets().find(x => x.key === key) || liveNets()[0]; let best = n.sites[0], c = -1;
  n.sites.forEach((s, j) => { const m = n.assign.filter(a => a === j).length; if (m > c){ c = m; best = s; } }); return best;
}
function otherSite(key){
  const n = liveNets().find(x => x.key === key) || liveNets()[0];
  const b = busiestSite(key);
  return n.sites.find(s => s !== b) || n.sites[0];
}
function fillDc(){
  const net = liveNets().find(n => n.key === $("c_net").value), dc = clear($("c_dc"));
  net.sites.forEach((s, j) => dc.appendChild(h("option", {value: s}, s + " · " + net.assign.filter(a => a === j).length + " stores")));
}
function buildControls(){
  const nets = liveNets(), days = dayOptions();
  const net = h("select", {id: "c_net"}); fillSelect(net, nets.filter(n => n.group.indexOf("Reference") === 0), "final:baseline");
  const dc = h("select", {id: "c_dc"});
  const day = h("select", {id: "c_day"}); days.forEach(d => day.appendChild(h("option", {value: d.key}, d.label))); day.value = "final:0";
  const num = (id, v, step, min) => h("input", {type: "number", id: id, value: v, step: step || "any", min: min});
  const c = clear($("controls"));
  [field("Network", net), field("DC (site)", dc), field("Demand day", day),
   field("Delivery window (HH:MM)", h("input", {type: "text", id: "c_noon", value: "12:00"})),
   field("Stop time (min)", num("c_stop", 15, 1, 0)), field("Speed (km/h)", num("c_speed", 35, 1, 5)),
   field("Capacity (cages)", num("c_cap", 48, 1, 1)), field("Max stops", num("c_stops", 25, 1, 1)),
   field("Max km", num("c_km", 250, 5, 10)), field("Max duration (h)", num("c_dur", 10, 0.5, 1)),
   field("Noon budget (% after window)", num("c_alpha", 2, 0.5, 0)),
   field("Owned fleet N (blank = network fleet)", h("input", {type: "number", id: "c_owned", min: 0, step: 1})),
   field("Spot-truck cap (blank = unlimited)", h("input", {type: "number", id: "c_cap_spot", min: 0, step: 1})),
   h("div", {}, h("label", {}, " "), h("button", {class: "primary", onclick: runLive}, "Solve"))].forEach(x => c.appendChild(x));
  net.addEventListener("change", fillDc); fillDc();
}
function parseHHMM(s){ const m = /^(\d{1,2}):(\d{2})$/.exec(String(s).trim()); return m ? (+m[1]) * 60 + (+m[2]) : NaN; }
const DEFAULTS = {noon: "12:00", stop: 15, speed: 35, cap: 48, stops: 25, km: 250, dur: 10, alpha: 2, owned: "", spotcap: ""};
function setControls(o){
  if (o.net){ $("c_net").value = o.net; fillDc(); }
  if (o.dc) $("c_dc").value = o.dc;
  if (o.day) $("c_day").value = o.day;
  const m = {noon: "c_noon", stop: "c_stop", speed: "c_speed", cap: "c_cap", stops: "c_stops", km: "c_km", dur: "c_dur", alpha: "c_alpha", owned: "c_owned", spotcap: "c_cap_spot"};
  Object.keys(m).forEach(k => { if (o.reset !== false && o[k] === undefined) $(m[k]).value = DEFAULTS[k]; if (o[k] !== undefined) $(m[k]).value = o[k]; });
}

function runLive(){
  const net = liveNets().find(n => n.key === $("c_net").value), site = $("c_dc").value, j = net.sites.indexOf(site);
  const members = []; net.assign.forEach((a, i) => { if (a === j) members.push(i); });
  const day = dayOptions().find(d => d.key === $("c_day").value);
  const noon = parseHHMM($("c_noon").value), banner = clear($("livebanner"));
  if (isNaN(noon)){ banner.appendChild(h("div", {class: "banner bad"}, "The window must be written as HH:MM, e.g. 12:00.")); return; }
  const P = mkParams({noon_min: noon, stop_min: +$("c_stop").value, speed_kmh: +$("c_speed").value, capacity: +$("c_cap").value, max_stops: Math.round(+$("c_stops").value),
    max_km: +$("c_km").value, max_route_min: +$("c_dur").value * 60, alpha: +$("c_alpha").value / 100});
  const ids = [site].concat(members.map(i => TOY.stores[i].id)), D = subMatrix(ids), q = [0].concat(members.map(i => day.demand[i]));
  const t0 = performance.now(), sol = solveDcDay(D, q, P), ms = performance.now() - t0;
  const ownedRaw = $("c_owned").value, owned = ownedRaw === "" ? net.fleet[site] : +ownedRaw;
  const capRaw = $("c_cap_spot").value, spotCap = capRaw === "" ? null : +capRaw;
  const out = clear($("liveout"));
  const pr = projector(640, 600, 30), svg = $("livemap"); mapBase(svg, pr);
  members.forEach(i => { const p = pr(TOY.stores[i].lat, TOY.stores[i].lon); svg.appendChild(sv("circle", {cx: p[0], cy: p[1], r: 2.6, fill: "var(--mute)", opacity: 0.5})); });
  const dp = siteById(site), dpp = pr(dp.lat, dp.lon);
  if (sol.status !== "ok"){
    banner.appendChild(h("div", {class: "banner bad"}, h("b", {}, "Infeasible: "), sol.message, " This is a failure case: no solution is shown as valid."));
    svg.appendChild(sv("rect", {x: dpp[0] - 8, y: dpp[1] - 8, width: 16, height: 16, fill: SITE_COL[siteIndex(site)], stroke: "var(--ink)", "stroke-width": 2}));
    out.appendChild(h("p", {class: "note"}, "Stores unreachable even alone: " + sol.forced_late.slice(0, 12).map(i => ids[i]).join(", ")));
    return;
  }
  const acc = account(sol, P, owned, spotCap), bad = validate(sol, D, q, P);
  if (acc.unserved.length) banner.appendChild(h("div", {class: "banner bad"}, h("b", {}, "The fleet cannot cover this day: "), acc.unserved.length + " stores are not delivered because only " + owned + " owned vehicles + " + (spotCap === null ? "unlimited" : spotCap) + " spot trucks are available, but " + acc.vehicles_needed + " vehicles are needed."));
  else if (sol.n12 > 0) banner.appendChild(h("div", {class: "banner warn"}, sol.n12 + " deliveries arrive after " + hhmm(P.noon_min) + " (noon budget allows " + sol.budget + ")."));
  else banner.appendChild(h("div", {class: "banner ok"}, "Feasible: all deliveries arrive by " + hhmm(P.noon_min) + ". Solved in " + ms.toFixed(0) + " ms; validator: " + (bad.length ? "VIOLATIONS " + bad.join(", ") : "no rule violated") + "."));
  sol.routes.forEach((r, k) => {
    const col = ROUTE_COL[k % ROUTE_COL.length], pts = [dpp].concat(r.stores.map(s => pr(TOY.stores[members[s - 1]].lat, TOY.stores[members[s - 1]].lon)), [dpp]);
    svg.appendChild(sv("polyline", {points: pts.map(p => p.join(",")).join(" "), fill: "none", stroke: col, "stroke-width": 1.8, opacity: 0.85}));
    r.stores.forEach(s => { const p = pr(TOY.stores[members[s - 1]].lat, TOY.stores[members[s - 1]].lon); svg.appendChild(sv("circle", {cx: p[0], cy: p[1], r: 3.2, fill: col})); });
  });
  svg.appendChild(sv("rect", {x: dpp[0] - 8, y: dpp[1] - 8, width: 16, height: 16, fill: SITE_COL[siteIndex(site)], stroke: "var(--ink)", "stroke-width": 2}));
  const k = (l, v, s) => h("div", {class: "kpi"}, h("b", {}, v), h("span", {}, l + (s ? " · " + s : "")));
  out.appendChild(h("div", {class: "grid g3"}, k("stores with demand", q.filter(x => x > 0).length), k("vehicles needed", acc.vehicles_needed, "owned " + owned + ", spot " + acc.spot), k("kilometres", acc.km.toFixed(0)),
    k("after " + hhmm(P.noon_min), sol.n12, "after 14:00: " + sol.n14), k("undelivered", acc.unserved.length), k("daily cost", fM2(acc.total) + " M")));
  out.appendChild(h("div", {class: "scroll", style: "max-height:260px;overflow-y:auto"}, h("table", {}, h("tr", {}, h("th", {}, "Route"), h("th", {}, "Stops"), h("th", {}, "Load"), h("th", {}, "km"), h("th", {}, "Last stop"), h("th", {}, "Return")),
    sol.routes.map((r, i) => h("tr", {}, h("td", {class: "l"}, "R" + (i + 1) + (r.forced_late ? " (forced late)" : "")), h("td", {}, r.stores.length), h("td", {}, r.load.toFixed(1)), h("td", {}, r.km.toFixed(0)), h("td", {}, hhmm(r.arrivals[r.arrivals.length - 1])), h("td", {}, hhmm(r.return_min)))))));
}

function applyTest(o){ setControls(Object.assign({net: "final:baseline"}, o)); runLive(); $("live").scrollIntoView(); }

function buildPresets(){
  const p = clear($("presets"));
  p.appendChild(h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0"})}, "1 · Regular (weekday, busiest DC)"));
  p.appendChild(h("button", {onclick: () => applyTest({dc: otherSite("final:baseline"), day: "final:" + weekendIndex()})}, "2 · Regular (weekend, other DC)"));
  p.appendChild(h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0", noon: "09:30"})}, "3 · Edge (window 09:30)"));
  p.appendChild(h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0", noon: "06:30"})}, "4 · Failure (window 06:30)"));
}
function weekendIndex(){
  const ix = TOY.days.final.strata.findIndex(s => s === "weekend");
  return ix >= 0 ? ix : 0;
}

function renderCases(){
  const c = R.cases, box = clear($("casesbody"));
  const rec = R.final.find(r => r.role === "recommended"), base = R.final.find(r => r.role === "baseline");
  box.appendChild(h("div", {class: "grid g2"},
    h("div", {},
      h("h3", {}, "1. Regular test"),
      h("p", {}, "Baseline network, busiest DC, a weekday from the final set, default vehicle limits and a 12:00 window. Expected: a feasible route set, service before noon."),
      h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0"})}, "Run live"),
      h("h3", {}, "2. Other regular test"),
      h("p", {}, "The other current DC on a weekend day. Residential demand is higher; office demand is lower. The same rules, a different geography (the river sits between the yards)."),
      h("button", {onclick: () => applyTest({dc: otherSite("final:baseline"), day: "final:" + weekendIndex()})}, "Run live")),
    h("div", {},
      h("h3", {}, "3. Edge test"),
      h("p", {}, c.boundary.description + " The live edge tightens the window to 09:30 so the clock binds: more vehicles, still a reported solution if the noon budget holds."),
      h("p", {class: "note"}, "Budget of one evaluation: " + c.budget_one.description + " It picks " + c.budget_one.chosen + " (E = " + fM(c.budget_one.E) + " M); pool best is " + fM(c.budget_one.optimum) + " M."),
      h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0", noon: "09:30"})}, "Run live"),
      h("h3", {}, "4. Failure test"),
      h("p", {}, "A number of DCs that cannot satisfy the capacity bounds is rejected with the reason, never shown as a valid network:"),
      h("ul", {}, (c.failure_k.length ? c.failure_k : [{reason: "none in this seed"}]).map(x => h("li", {}, x.reason))),
      h("p", {}, "Live failure: window 06:30. Stores that cannot be reached even with a dedicated vehicle exceed the noon budget; the solver reports infeasible rather than a fake route plan. A second live failure is a day the owned fleet cannot cover (spot cap = 0) at " + c.failure_day.site + " on " + c.failure_day.date + ": " + c.failure_day.vehicles_needed + " vehicles needed, " + c.failure_day.owned + " owned → " + c.failure_day.stores_undelivered + " undelivered."),
      h("button", {onclick: () => applyTest({dc: busiestSite("final:baseline"), day: "final:0", noon: "06:30"})}, "Run infeasible window"),
      h("button", {onclick: () => applyTest({net: "final:recommended", dc: c.failure_day.site, day: "final:" + worstFinal(), spotcap: 0})}, "Run uncovered day"))));
  if (base && rec) box.appendChild(h("p", {class: "note"}, "Precomputed baseline " + fM(base.final.total) + " M vs recommended " + rec.sites.join("+") + " " + fM(rec.final.total) + " M per month on the final days."));
}
function worstFinal(){
  return TOY.days.final.demand.map(d => d.reduce((a, b) => a + b, 0)).reduce((bi, v, i, a) => v > a[bi] ? i : bi, 0);
}

function renderMethod(){
  const box = clear($("methodbody"));
  box.appendChild(h("ul", {},
    h("li", {}, "Pipeline: generator → distances → demand model → lower level → E on the baseline → G candidates → comparison. Python writes the numbers; this page embeds them."),
    h("li", {}, "Instance: " + TOY.meta.n_stores + " stores, 2 current DCs, 4 candidate sites, one river with three bridges. Store-count bounds " + TOY.meta.min_stores + "–" + TOY.meta.max_stores + "."),
    h("li", {}, "Distances: " + TOY.meta.distance_source + ". Cached in distances/cache/. Not OSRM; the Field Manager’s river is modelled by forcing every crossing onto a bridge."),
    h("li", {}, "Lower level: Clarke–Wright + relocate/swap/2-opt + a noon-budget pass. Every vehicle priced as a spot truck, then accounted against the owned fleet."),
    h("li", {}, "Upper level: every subset of the four sites. G is fixed cost plus a road-distance assignment; E is routing on sampled days. Seed " + TOY.meta.seed + ", " + TOY.meta.solves + " solves, " + TOY.meta.runtime_seconds.toFixed(0) + " s."),
    h("li", {}, "Reproduce precomputed figures with ", h("code", {}, TOY.meta.command), ". Open this file directly, or serve ui/ on localhost with ", h("code", {}, "python run_demo.py"), ".")));
}

function renderOverview(){
  const base = R.final.find(r => r.role === "baseline"), rec = R.final.find(r => r.role === "recommended");
  const k = (v, l) => h("div", {class: "kpi"}, h("b", {}, v), h("span", {}, l)), g = clear($("kpis"));
  g.appendChild(k(fM(base.final.total) + " M", "baseline (2 DCs), KRW / month"));
  g.appendChild(k(rec ? fM(rec.final.total) + " M" : "—", rec ? "recommended " + rec.sites.join(" + ") : "recommended"));
  g.appendChild(k(rec && rec.saving_final ? fP(rec.saving_final.pct) : "—", "saving on final days vs baseline"));
  g.appendChild(k(R.metrics.spearman.toFixed(2) + " / " + fP(R.metrics.decision_loss_pct, 1), "G–E rank correlation / G-best loss"));
  const t = clear($("overview-text"));
  t.appendChild(h("p", {}, "Upper level: how many DCs and where (four leasable sites). Lower level: one DC’s routes on a sampled demand day. The assignment of stores to open DCs is re-solved inside each evaluation. All figures are fictional."));
}

function buildNav(){
  const nav = clear($("nav"));
  [["overview", "Overview"], ["map", "Map"], ["sweep", "Sweep"], ["gve", "G vs E"], ["log", "Log"], ["compare", "Baseline"], ["live", "Live solver"], ["cases", "Tests"], ["method", "Method"]].forEach(x => nav.appendChild(h("a", {href: "#" + x[0]}, x[1])));
}
function parityCheck(){
  if (!TOY.parity) return;
  let ok = 0, total = 0, msgs = [];
  TOY.parity.forEach(c => {
    total++;
    const ids = [c.site].concat(c.store_ids), D = subMatrix(ids), P = mkParams(c.params), sol = solveDcDay(D, c.q, P), e = c.expected;
    const same = sol.status === e.status && sol.vehicles === e.vehicles && Math.abs(sol.km - e.km) < 1e-4 && sol.n12 === e.n12 && sol.n14 === e.n14 &&
      JSON.stringify(sol.routes.map(r => r.stores)) === JSON.stringify(e.routes);
    if (same) ok++; else msgs.push(c.label);
  });
  const txt = ok === total ? ("parity with Python: " + ok + "/" + total + " reference cases identical") : ("PARITY MISMATCH on: " + msgs.join("; "));
  const el = $("parity"); if (el){ el.textContent = txt; el.style.color = ok === total ? "var(--ok)" : "var(--bad)"; }
}

function init(){
  if (typeof TOY === "undefined" || !TOY){
    document.body.insertBefore(h("p", {class: "banner bad", style: "margin:16px 24px"}, "No toy_data.js yet. Run python run_demo.py --build, then reload."), document.querySelector("main"));
    return;
  }
  buildDist(); buildNets(); buildNav(); renderOverview();
  const sel = $("mapsel");
  const rec = R.final.find(r => r.role === "recommended");
  fillSelect(sel, NETS, rec ? "final:recommended" : "final:baseline");
  sel.addEventListener("change", () => renderMap(sel.value)); renderMap(sel.value);
  renderSweep(); renderScatter(); renderCurve(); renderLog(); renderCompare();
  buildControls(); buildPresets(); renderCases(); renderMethod();
  setControls({net: "final:baseline", dc: busiestSite("final:baseline"), day: "final:0"}); runLive();
  setTimeout(parityCheck, 30);
}
if (typeof document !== "undefined") init();
