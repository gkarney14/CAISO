// BESS arbitrage simulation engine. Pure functions, no DOM. Works in browser and Node.
(function (root) {
  const PT = "America/Los_Angeles";
  const dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: PT, year: "numeric", month: "2-digit", day: "2-digit" });
  const hourFmt = new Intl.DateTimeFormat("en-US", { timeZone: PT, hour: "numeric", hourCycle: "h23" });
  const dayKey = (s) => dayFmt.format(new Date(s * 1000));
  const hourOf = (s) => parseInt(hourFmt.format(new Date(s * 1000)), 10) % 24;

  // Build per-day structures: {day, da[24], rtH[24], rt15:[[hour,price]...]}
  function buildDays(series) {
    const days = new Map();
    const get = (k) => { if (!days.has(k)) days.set(k, { day: k, da: Array(24).fill(null), rtSum: Array(24).fill(0), rtN: Array(24).fill(0), rt15: [] }); return days.get(k); };
    for (const [s, p] of series.da) { const d = get(dayKey(s)); d.da[hourOf(s)] = p; }
    for (const [s, p] of series.rt) { const d = get(dayKey(s)); const h = hourOf(s); d.rtSum[h] += p; d.rtN[h] += 1; d.rt15.push([h, p]); }
    const out = [];
    for (const d of [...days.values()].sort((a, b) => a.day < b.day ? -1 : 1)) {
      d.rtH = d.rtSum.map((v, i) => d.rtN[i] ? v / d.rtN[i] : null);
      d.complete = d.da.every((v) => v !== null) && d.rtH.every((v) => v !== null);
      out.push(d);
    }
    return out;
  }

  // Exact hourly DP: SOC in whole "hours of energy" (0..H), at most H*cycles discharge-hours, start/end empty.
  // Charging at P for 1 h stores P MWh; discharging removes P MWh and sells P*eta. degr in $/MWh discharged.
  function optimizeDay(prices, H, cycles, eta, degr) {
    const T = prices.length, D = H * cycles;
    const NEG = -1e18;
    const key = (s, d) => s * (D + 1) + d;
    let V = new Float64Array((H + 1) * (D + 1)).fill(NEG); V[key(0, 0)] = 0;
    const back = [];
    for (let t = 0; t < T; t++) {
      const p = prices[t];
      const NV = new Float64Array(V.length).fill(NEG); const B = new Int8Array(V.length);
      for (let s = 0; s <= H; s++) for (let d = 0; d <= D; d++) {
        const v = V[key(s, d)]; if (v === NEG) continue;
        // idle
        if (v > NV[key(s, d)]) { NV[key(s, d)] = v; B[key(s, d)] = 0; }
        // charge
        if (s < H) { const nv = v - p; const k = key(s + 1, d); if (nv > NV[k]) { NV[k] = nv; B[k] = 1; } }
        // discharge
        if (s > 0 && d < D) { const nv = v + p * eta - degr; const k = key(s - 1, d + 1); if (nv > NV[k]) { NV[k] = nv; B[k] = -1; } }
      }
      back.push(B); V = NV;
    }
    // best end state with empty battery
    let best = NEG, bd = 0;
    for (let d = 0; d <= D; d++) if (V[key(0, d)] > best) { best = V[key(0, d)]; bd = d; }
    const act = Array(T).fill(0); let s = 0, d = bd;
    for (let t = T - 1; t >= 0; t--) {
      const a = back[t][key(s, d)]; act[t] = a;
      if (a === 1) s -= 1; else if (a === -1) { s += 1; d -= 1; }
    }
    return act; // +1 charge, -1 discharge, 0 idle (per MW, per hour)
  }

  function settle(act, prices, P, eta, degr) {
    let rev = 0, chMWh = 0, disMWh = 0, chCost = 0, disRev = 0, negMWh = 0;
    for (let t = 0; t < act.length; t++) {
      const p = prices[t];
      if (act[t] === 1) { rev -= p * P; chMWh += P; chCost += p * P; if (p < 0) negMWh += P; }
      else if (act[t] === -1) { rev += p * P * eta - degr * P; disMWh += P * eta; disRev += p * P * eta; }
    }
    return { rev, chMWh, disMWh, chCost, disRev, negMWh };
  }

  // Threshold trigger on 15-min RT prices, SOC carried across days. Daily discharge cap = cycles * E.
  function thresholdRun(days, P, H, eta, degr, lo, hi, cycles) {
    const E = P * H, step = 0.25; let soc = 0; const res = [];
    for (const d of days) {
      let rev = 0, chMWh = 0, disMWh = 0, chCost = 0, disRev = 0, negMWh = 0, disCap = cycles * E;
      const socTrace = Array(24).fill(0);
      for (const [h, p] of d.rt15) {
        if (p <= lo && soc < E) {
          const e = Math.min(P * step, E - soc); soc += e; rev -= p * e; chMWh += e; chCost += p * e; if (p < 0) negMWh += e;
        } else if (p >= hi && soc > 0 && disCap > 0) {
          const e = Math.min(P * step, soc, disCap); soc -= e; disCap -= e; rev += p * e * eta - degr * e; disMWh += e * eta; disRev += p * e * eta;
        }
        socTrace[h] = soc;
      }
      res.push({ day: d.day, rev, chMWh, disMWh, chCost, disRev, negMWh, socTrace });
    }
    return res;
  }

  function simulate(days, cfg) {
    const { P, H, eta, degr, cycles, lo, hi } = cfg;
    const full = days.filter((d) => d.complete);
    const S = { pf: [], da: [], darts: [], thr: [] };
    for (const d of full) {
      const aRT = optimizeDay(d.rtH, H, cycles, eta, degr);
      const aDA = optimizeDay(d.da, H, cycles, eta, degr);
      S.pf.push({ day: d.day, act: aRT, ...settle(aRT, d.rtH, P, eta, degr) });
      S.da.push({ day: d.day, act: aDA, ...settle(aDA, d.da, P, eta, degr) });
      S.darts.push({ day: d.day, act: aDA, ...settle(aDA, d.rtH, P, eta, degr) });
    }
    S.thr = thresholdRun(full, P, H, eta, degr, lo, hi, cycles);
    return { days: full, S };
  }

  function summarize(rows, P, H) {
    const n = rows.length || 1;
    const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
    const rev = sum("rev"), ch = sum("chMWh"), dis = sum("disMWh");
    return {
      rev, perDay: rev / n, perKwMonth: rev / (P * 1000) / (n / 30.4375),
      cycles: ch / (P * H), avgCh: ch ? sum("chCost") / ch : 0, avgDis: dis ? sum("disRev") / dis : 0,
      negMWh: sum("negMWh"), days: rows.length,
    };
  }

  const api = { buildDays, optimizeDay, settle, thresholdRun, simulate, summarize, dayKey, hourOf };
  if (typeof module !== "undefined") module.exports = api; else root.BessSim = api;
})(this);
