// Line-by-line port of lower_level/solver.py. Do not change one without the other.
const EPS = 1e-9, TOL = 1e-6;
let BASE_PARAMS = {speed_kmh:35, stop_min:15, depart_min:300, noon_min:720, late_min:840, capacity:48, max_stops:25,
  max_km:250, max_route_min:600, alpha:0.02, c_fuel:350, c_spot:550000, p_late:120000, p_stockout:500000, max_passes:4};
function mkParams(o){ return Object.assign({}, BASE_PARAMS, o || {}); }

class Solver {
  constructor(D, q, P, alpha){
    this.D = D; this.q = q; this.P = P; this.n = D.length - 1;
    const a = (alpha === undefined || alpha === null) ? P.alpha : alpha;
    this.budget = Math.floor(a * this.n + 1e-9);
    this.mpk = 60.0 / P.speed_kmh; this.R = []; this.S = [];
  }
  ev(seq){
    const D = this.D, q = this.q, P = this.P, mpk = this.mpk;
    let km = 0.0, t = P.depart_min, prev = 0, load = 0.0, n12 = 0, n14 = 0;
    for (const s of seq){
      const leg = D[prev][s]; km += leg; t += leg * mpk;
      if (t > P.noon_min + EPS) n12 += 1;
      if (t > P.late_min + EPS) n14 += 1;
      t += P.stop_min; load += q[s]; prev = s;
    }
    const leg = D[prev][0]; km += leg; t += leg * mpk;
    return [km, t, load, n12, n14];
  }
  ok(seq, s, hard){
    const P = this.P;
    return s[2] <= P.capacity + EPS && seq.length <= P.max_stops && s[0] <= P.max_km + EPS &&
      (s[1] - P.depart_min) <= P.max_route_min + EPS && (!hard || s[3] === 0);
  }
  savings(nodes){
    const D = this.D, D0 = D[0];
    const routes = new Map(), rid = new Map();
    for (const i of nodes){ routes.set(i, [i]); rid.set(i, i); }
    const sav = [];
    for (let a = 0; a < nodes.length; a++){
      const i = nodes[a];
      for (let b = a + 1; b < nodes.length; b++){
        const j = nodes[b]; const s = D0[i] + D0[j] - D[i][j];
        if (s > EPS) sav.push([-s, i, j]);
      }
    }
    sav.sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]) || (x[2] - y[2]));
    for (const e of sav){
      const i = e[1], j = e[2];
      const ri = rid.get(i), rj = rid.get(j);
      if (ri === rj) continue;
      const A = routes.get(ri), B = routes.get(rj);
      let m;
      if (A[A.length - 1] === i && B[0] === j) m = A.concat(B);
      else if (A[0] === i && B[B.length - 1] === j) m = B.concat(A);
      else if (A[A.length - 1] === i && B[B.length - 1] === j) m = A.concat(B.slice().reverse());
      else if (A[0] === i && B[0] === j) m = A.slice().reverse().concat(B);
      else continue;
      const s = this.ev(m);
      if (this.ok(m, s, true)){
        routes.set(ri, m); routes.delete(rj);
        for (const v of B) rid.set(v, ri);
      }
    }
    this.R = Array.from(routes.values()).sort((a, b) => a[0] - b[0]);
    this.S = this.R.map(r => this.ev(r));
  }
  twoOpt(){
    let imp = false;
    for (let r = 0; r < this.R.length; r++){
      let again = true;
      while (again){
        again = false;
        const seq = this.R[r], m = seq.length;
        outer: for (let a = 0; a < m - 1; a++){
          for (let b = a + 1; b < m; b++){
            const nw = seq.slice(0, a).concat(seq.slice(a, b + 1).reverse(), seq.slice(b + 1));
            const s = this.ev(nw);
            if (this.ok(nw, s, true) && s[0] < this.S[r][0] - 1e-7){
              this.R[r] = nw; this.S[r] = s; imp = true; again = true; break outer;
            }
          }
        }
      }
    }
    return imp;
  }
  relocate(){
    const P = this.P, q = this.q, cf = P.c_fuel, cs = P.c_spot;
    let imp = false, r1 = 0;
    while (r1 < this.R.length){
      let p = 0, deleted = false;
      while (p < this.R[r1].length){
        const seq1 = this.R[r1], node = seq1[p];
        const new1 = seq1.slice(0, p).concat(seq1.slice(p + 1));
        const s1 = new1.length ? this.ev(new1) : [0.0, 0.0, 0.0, 0, 0];
        const old1 = this.S[r1][0];
        let best = null, bestDelta = -TOL;
        for (let r2 = 0; r2 < this.R.length; r2++){
          if (r2 === r1) continue;
          const seq2 = this.R[r2];
          if (this.S[r2][2] + q[node] > P.capacity + EPS || seq2.length + 1 > P.max_stops) continue;
          for (let pos = 0; pos <= seq2.length; pos++){
            const new2 = seq2.slice(0, pos).concat([node], seq2.slice(pos));
            const s2 = this.ev(new2);
            if (!this.ok(new2, s2, true)) continue;
            const delta = cf * (s1[0] + s2[0] - old1 - this.S[r2][0]) - (new1.length ? 0.0 : cs);
            if (delta < bestDelta){ bestDelta = delta; best = [r2, new2, s2]; }
          }
        }
        if (best !== null){
          const r2 = best[0], new2 = best[1], s2 = best[2];
          this.R[r2] = new2; this.S[r2] = s2; imp = true;
          if (new1.length){ this.R[r1] = new1; this.S[r1] = s1; continue; }
          this.R.splice(r1, 1); this.S.splice(r1, 1); deleted = true; break;
        }
        p += 1;
      }
      if (!deleted) r1 += 1;
    }
    return imp;
  }
  swap(){
    const P = this.P, q = this.q, cf = P.c_fuel;
    let imp = false;
    for (let r1 = 0; r1 < this.R.length; r1++){
      for (let r2 = r1 + 1; r2 < this.R.length; r2++){
        while (true){
          let found = false;
          const a = this.R[r1], b = this.R[r2];
          search: for (let x = 0; x < a.length; x++){
            for (let y = 0; y < b.length; y++){
              const i = a[x], j = b[y];
              const l1 = this.S[r1][2] - q[i] + q[j], l2 = this.S[r2][2] - q[j] + q[i];
              if (l1 > P.capacity + EPS || l2 > P.capacity + EPS) continue;
              const n1 = a.slice(0, x).concat([j], a.slice(x + 1));
              const e1 = this.ev(n1);
              if (!this.ok(n1, e1, true)) continue;
              const n2 = b.slice(0, y).concat([i], b.slice(y + 1));
              const e2 = this.ev(n2);
              if (!this.ok(n2, e2, true)) continue;
              if (cf * (e1[0] + e2[0] - this.S[r1][0] - this.S[r2][0]) < -TOL){
                this.R[r1] = n1; this.S[r1] = e1; this.R[r2] = n2; this.S[r2] = e2; found = true; break search;
              }
            }
          }
          if (!found) break;
          imp = true;
        }
      }
    }
    return imp;
  }
  reinsert(r, hard, bEff){
    const P = this.P, q = this.q, D0 = this.D[0], cf = P.c_fuel, pl = P.p_late;
    const tmpR = [], tmpS = [];
    for (let k = 0; k < this.R.length; k++) if (k !== r){ tmpR.push(this.R[k].slice()); tmpS.push(this.S[k]); }
    let n12Tot = 0;
    for (const s of this.S) n12Tot += s[3];
    n12Tot -= this.S[r][3];
    const nodes = this.R[r].slice().sort((u, v) => (D0[v] - D0[u]) || (u - v));
    for (const node of nodes){
      let best = null, bc = Infinity;
      for (let r2 = 0; r2 < tmpR.length; r2++){
        const seq2 = tmpR[r2];
        if (tmpS[r2][2] + q[node] > P.capacity + EPS || seq2.length + 1 > P.max_stops) continue;
        for (let pos = 0; pos <= seq2.length; pos++){
          const new2 = seq2.slice(0, pos).concat([node], seq2.slice(pos));
          const s2 = this.ev(new2);
          if (!this.ok(new2, s2, hard)) continue;
          const dn12 = s2[3] - tmpS[r2][3];
          if (!hard && n12Tot + dn12 > bEff) continue;
          const c = cf * (s2[0] - tmpS[r2][0]) + (hard ? 0.0 : pl * (s2[4] - tmpS[r2][4]));
          if (c < bc - 1e-12){ bc = c; best = [r2, new2, s2, dn12]; }
        }
      }
      if (best === null) return null;
      tmpR[best[0]] = best[1]; tmpS[best[0]] = best[2]; n12Tot += best[3];
    }
    return [tmpR, tmpS];
  }
  eliminate(hard, bEff){
    const P = this.P;
    let imp = false;
    while (true){
      let success = false;
      const order = this.R.map((_, k) => k).sort((a, b) => (this.R[a].length - this.R[b].length) || (a - b));
      for (const r of order){
        if (this.R.length <= 1) break;
        const res = this.reinsert(r, hard, bEff);
        if (res === null) continue;
        const tmpR = res[0], tmpS = res[1];
        let newKm = 0.0, oldKm = 0.0, n14New = 0, n14Old = 0;
        for (const st of tmpS){ newKm += st[0]; n14New += st[4]; }
        for (const st of this.S){ oldKm += st[0]; n14Old += st[4]; }
        const delta = P.c_fuel * (newKm - oldKm) + (hard ? 0.0 : P.p_late * (n14New - n14Old)) - P.c_spot;
        if (delta < -TOL){ this.R = tmpR; this.S = tmpS; success = true; imp = true; break; }
      }
      if (!success) break;
    }
    return imp;
  }
}

function solo(D, P, i){
  const mpk = 60.0 / P.speed_kmh;
  const arr = P.depart_min + D[0][i] * mpk;
  const ret = arr + P.stop_min + D[i][0] * mpk;
  return [2 * D[0][i], arr, ret];
}
function hhmm(m){ m = Math.round(m); return String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); }

function routeDict(sv, seq, forced){
  const P = sv.P, D = sv.D, q = sv.q;
  let km = 0.0, t = P.depart_min, prev = 0; const arr = [];
  for (const s of seq){ km += D[prev][s]; t += D[prev][s] * sv.mpk; arr.push(t); t += P.stop_min; prev = s; }
  km += D[prev][0]; t += D[prev][0] * sv.mpk;
  let load = 0.0; for (const s of seq) load += q[s];
  return {stores: seq.slice(), km: km, load: load, return_min: t, arrivals: arr,
    n12: arr.filter(a => a > P.noon_min + EPS).length, n14: arr.filter(a => a > P.late_min + EPS).length, forced_late: forced};
}

function solveDcDay(D, q, P, alpha){
  const n = D.length - 1;
  const sv = new Solver(D, q, P, alpha);
  const unserved = [], forced = [], main = [];
  for (let i = 1; i <= n; i++){
    if (q[i] <= 0) continue;
    const so = solo(D, P, i);
    if (q[i] > P.capacity + EPS || so[0] > P.max_km + EPS || so[2] - P.depart_min > P.max_route_min + EPS) unserved.push(i);
    else if (so[1] > P.noon_min + EPS) forced.push(i);
    else main.push(i);
  }
  if (forced.length > sv.budget){
    return {status: 'infeasible', routes: [], unserved: unserved, forced_late: forced, vehicles: 0, km: 0.0, n12: forced.length, n14: 0,
      budget: sv.budget, n_stores: n,
      message: forced.length + ' stores cannot be reached by ' + hhmm(P.noon_min) + ' even with a dedicated vehicle, but the noon budget allows only ' +
        sv.budget + ' of ' + n + ' stores after noon. The service-level constraint cannot be met; no routes are produced.'};
  }
  if (main.length){
    sv.savings(main);
    for (let pass = 0; pass < P.max_passes; pass++){
      let ch = sv.twoOpt();
      ch = sv.relocate() || ch;
      ch = sv.swap() || ch;
      ch = sv.eliminate(true, 0) || ch;
      if (!ch) break;
    }
    const bEff = sv.budget - forced.length;
    if (bEff > 0) sv.eliminate(false, bEff);
  }
  const routes = sv.R.map(seq => routeDict(sv, seq, false));
  for (const i of forced) routes.push(routeDict(sv, [i], true));
  routes.sort((a, b) => a.stores[0] - b.stores[0]);
  let km = 0, n12 = 0, n14 = 0;
  for (const r of routes){ km += r.km; n12 += r.n12; n14 += r.n14; }
  return {status: 'ok', message: '', routes: routes, unserved: unserved, forced_late: forced, vehicles: routes.length, km: km,
    n12: n12, n14: n14, budget: sv.budget, n_stores: n};
}

function account(sol, P, owned, spotCap){
  const routes = sol.routes, V = routes.length;
  let spot = Math.max(0, V - owned);
  let unserved = sol.unserved.slice();
  const drop = new Set();
  if (spotCap !== null && spotCap !== undefined && spot > spotCap){
    const k = spot - spotCap;
    const order = routes.map((_, i) => i).sort((a, b) => (routes[a].load - routes[b].load) || (a - b)).slice(0, k);
    for (const r of order){ drop.add(r); unserved = unserved.concat(routes[r].stores); }
    spot = spotCap;
  }
  const keep = routes.filter((_, i) => !drop.has(i));
  let km = 0, n12 = 0, n14 = 0, delivered = 0;
  for (const r of keep){ km += r.km; n12 += r.n12; n14 += r.n14; delivered += r.stores.length; }
  const fuel = P.c_fuel * km, spotCost = P.c_spot * spot, failure = P.p_late * n14 + P.p_stockout * unserved.length;
  return {vehicles_needed: V, vehicles_run: keep.length, km: km, spot: spot, n12: n12, n14: n14, delivered: delivered,
    unserved: unserved.slice().sort((a, b) => a - b), fuel: fuel, spot_cost: spotCost, failure: failure, total: fuel + spotCost + failure};
}

function validate(sol, D, q, P, alpha){
  const n = D.length - 1, a = (alpha === undefined || alpha === null) ? P.alpha : alpha;
  const budget = Math.floor(a * n + 1e-9), mpk = 60.0 / P.speed_kmh, bad = [];
  let seen = [], n12 = 0;
  for (const r of sol.routes){
    let t = P.depart_min, prev = 0, km = 0, load = 0;
    for (const s of r.stores){ km += D[prev][s]; t += D[prev][s] * mpk; if (t > P.noon_min + EPS) n12++; t += P.stop_min; load += q[s]; prev = s; }
    km += D[prev][0]; t += D[prev][0] * mpk;
    if (load > P.capacity + 1e-6) bad.push('load');
    if (r.stores.length > P.max_stops) bad.push('stops');
    if (km > P.max_km + 1e-6) bad.push('km');
    if (t - P.depart_min > P.max_route_min + 1e-6) bad.push('duration');
    seen = seen.concat(r.stores);
  }
  const need = []; for (let i = 1; i <= n; i++) if (q[i] > 0) need.push(i);
  const got = seen.concat(sol.unserved).sort((x, y) => x - y);
  if (JSON.stringify(got) !== JSON.stringify(need)) bad.push('cover');
  if (n12 > budget) bad.push('noon-budget');
  return bad;
}
