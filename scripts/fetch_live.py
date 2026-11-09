"""Fetch live CAISO data for the public dashboard: RT 5-min LMP + components (yesterday 00:00 PT to now)
for the cohort pnodes and hubs, and DA hourly LMP (today + tomorrow) for the node and NP15.
Writes data/live.json."""
import io, json, time, zipfile, urllib.request, csv, datetime as dt
from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los_Angeles")
COHORT = {"SCARLT_7_N011": "scarlet", "SCARLT_7_ND001": "sonrisa", "SCARLT_7_N001": "s1a_bess", "SCARLT_7_N002": "s1b_bess",
          "SCARLT_7_N010": "s2_bess", "SCARLT_7_CN1": "s1a_pv", "SCARLT_7_CN2": "s1b_pv", "SCARLT_7_N005": "s2_pv", "SCARLT_7_ND002": "sonrisa_pv"}
HUBS = {"TH_NP15_GEN-APND": "np15", "TH_ZP26_GEN-APND": "zp26", "TH_SP15_GEN-APND": "sp15"}
RT_NODES = {**COHORT, **HUBS}
DA_NODES = {**COHORT, "TH_NP15_GEN-APND": "np15"}
def chunks(d, n=6):
    k = list(d); return [{x: d[x] for x in k[i:i + n]} for i in range(0, len(k), n)]
U = lambda x: x.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H:%M-0000")
def get(q, v, m, nodes, s, e):
    u = (f"https://oasis.caiso.com/oasisapi/SingleZip?resultformat=6&queryname={q}&version={v}&market_run_id={m}"
         f"&node={','.join(nodes)}&startdatetime={U(s)}&enddatetime={U(e)}")
    for a in range(4):
        try:
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=120).read())); f = z.namelist()[0]
            if not f.endswith(".csv"):
                if b"No data returned" in z.read(f): return []
                raise RuntimeError(z.read(f)[:200])
            return list(csv.DictReader(io.TextIOWrapper(z.open(f))))
        except Exception as ex:
            print("retry", a, ex); time.sleep(8 * (a + 1))
    return []
now = dt.datetime.now(PT); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
val = lambda r: float(r.get("VALUE") or r.get("MW") or r.get("PRC"))
rt = {}
rows = []
for c in chunks(RT_NODES):
    rows += get("PRC_INTVL_LMP", 3, "RTM", c, today - dt.timedelta(days=1), now + dt.timedelta(minutes=10)); time.sleep(5)
for r in rows:
    n = RT_NODES.get(r["NODE"]); typ = r["LMP_TYPE"]
    if not n or typ not in ("LMP", "MCE", "MCC", "MCL", "MGHG"): continue
    t = int(dt.datetime.fromisoformat(r["INTERVALSTARTTIME_GMT"]).timestamp())
    rt.setdefault(n, {}).setdefault(t, {})[typ] = round(val(r), 2)
time.sleep(6)
da = {}
rows = []
for c in chunks(DA_NODES):
    rows += get("PRC_LMP", 12, "DAM", c, today, today + dt.timedelta(days=2)); time.sleep(5)
for r in rows:
    if r["LMP_TYPE"] != "LMP" or r["NODE"] not in DA_NODES: continue
    t = int(dt.datetime.fromisoformat(r["INTERVALSTARTTIME_GMT"]).timestamp())
    da.setdefault(DA_NODES[r["NODE"]], {})[t] = round(val(r), 2)
out_da = {k: {t: p for t, p in v.items()} for k, v in da.items()}
# --- Irradiance forecast at the Scarlet site (public coordinates, Global Energy Monitor) ---
SITE = (36.5975, -120.3623)
irr = []
try:
    import pandas as pd, pvlib
    q = (f"https://api.open-meteo.com/v1/forecast?latitude={SITE[0]}&longitude={SITE[1]}"
         "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,cloud_cover,temperature_2m"
         "&temperature_unit=fahrenheit&timezone=UTC&forecast_days=7&past_days=31")
    h = json.load(urllib.request.urlopen(q, timeout=60))["hourly"]
    end = pd.to_datetime(h["time"], utc=True)            # values are means over the hour ending at this time
    mid = end - pd.Timedelta(minutes=30)
    sp = pvlib.solarposition.get_solarposition(mid, SITE[0], SITE[1])
    trk = pvlib.tracking.singleaxis(sp["apparent_zenith"], sp["azimuth"], axis_tilt=0, axis_azimuth=180,
                                    max_angle=60, backtrack=True, gcr=0.35)
    ghi, dni, dhi = (pd.Series(h[k], index=end).fillna(0) for k in ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation"))
    poa = pvlib.irradiance.get_total_irradiance(trk["surface_tilt"].fillna(0), trk["surface_azimuth"].fillna(180),
              sp["apparent_zenith"].values, sp["azimuth"].values, dni.values, ghi.values, dhi.values, albedo=0.2)["poa_global"]
    for i, t in enumerate(end):
        st = int((t - pd.Timedelta(hours=1)).timestamp())   # hour start, to match DA interval start
        irr.append([st, round(ghi.iloc[i]), round(float(poa.iloc[i]) if poa.iloc[i] == poa.iloc[i] else 0), round(dni.iloc[i]), h["cloud_cover"][i], h["temperature_2m"][i]])
except Exception as ex:
    print("irradiance skipped:", ex)
# --- Modeled node price from irradiance: per-HE linear fit of DA price on POA, last 30 days ---
fc = {"model": None, "rows": []}
try:
    import numpy as np
    P = json.load(open("data/prices.json"))["series"]["node"]["da"]     # [[epoch_start, price]], cohort pnode
    pmap = {int(t): p for t, p in P}
    for t, p in out_da.get("scarlet", {}).items(): pmap[int(t)] = p
    imap = {r[0]: r[2] for r in irr}
    now_s = time.time(); coef = {}; err = []
    for h in range(24):
        xs, ys = [], []
        for t, poa in imap.items():
            if t < now_s - 86400 and t >= now_s - 31 * 86400 and t in pmap and dt.datetime.fromtimestamp(t, PT).hour == h:
                xs.append(poa); ys.append(pmap[t])
        if len(xs) >= 10:
            X = np.array(xs); Y = np.array(ys)
            b, a = (np.polyfit(X, Y, 1) if X.std() > 5 else (0.0, Y.mean()))
            coef[h] = (float(a), float(b)); err += list(np.abs(a + b * X - Y))
    da_known = set(pmap)
    for t, poa in sorted(imap.items()):
        if t < int(today.timestamp()) or t >= int(today.timestamp()) + 4 * 86400: continue
        h = dt.datetime.fromtimestamp(t, PT).hour
        model = round(coef[h][0] + coef[h][1] * poa, 2) if h in coef else None
        fc["rows"].append([t, poa, model, pmap.get(t)])
    fc["model"] = {"type": "per-HE linear: DA price ~ POA", "days": 30, "mae": round(float(np.mean(err)), 2) if err else None,
                   "midday_slope": round(float(np.mean([coef[h][1] for h in range(10, 15) if h in coef])) * 100, 2) if coef else None}
except Exception as ex:
    print("price model skipped:", ex)
# --- 1-minute POA counts per hour (>=400, >=600 W/m2) from Open-Meteo 15-min forecast ---
pts = {}
try:
    import pandas as pd, numpy as np, pvlib
    q = (f"https://api.open-meteo.com/v1/forecast?latitude={SITE[0]}&longitude={SITE[1]}"
         "&minutely_15=shortwave_radiation,direct_normal_irradiance,diffuse_radiation&timezone=UTC&forecast_days=4")
    m = json.load(urllib.request.urlopen(q, timeout=60))["minutely_15"]
    end = pd.to_datetime(m["time"], utc=True); mid = end - pd.Timedelta(minutes=7.5)      # 15-min means ending at label
    sp = pvlib.solarposition.get_solarposition(mid, SITE[0], SITE[1])
    trk = pvlib.tracking.singleaxis(sp["apparent_zenith"], sp["azimuth"], axis_tilt=0, axis_azimuth=180, max_angle=60, backtrack=True, gcr=0.35)
    g, dn, df = (np.nan_to_num(np.array(m[k], dtype=float)) for k in ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation"))
    poa15 = pvlib.irradiance.get_total_irradiance(trk["surface_tilt"].fillna(0), trk["surface_azimuth"].fillna(180),
               sp["apparent_zenith"].values, sp["azimuth"].values, dn, g, df, albedo=0.2)["poa_global"].fillna(0).clip(lower=0)
    s15 = pd.Series(poa15.values, index=mid)
    minute = pd.date_range(mid[0].floor("h"), mid[-1].ceil("h"), freq="1min", inclusive="left") + pd.Timedelta(seconds=30)
    p1 = s15.reindex(s15.index.union(minute)).interpolate(method="time", limit_area="inside").reindex(minute).fillna(0)
    hr = (p1.index - pd.Timedelta(seconds=30)).floor("h")
    for h, grp in p1.groupby(hr):
        pts[int(h.timestamp())] = [int((grp >= 400).sum()), int((grp >= 600).sum())]
except Exception as ex:
    print("1-min counts skipped:", ex)
for r in fc.get("rows", []):
    r += pts.get(r[0], [None, None])
out = {"generated": int(time.time()), "fc": fc, "irr_site": {"lat": SITE[0], "lon": SITE[1], "tracker": "1-axis N-S, +/-60 deg, backtracking, GCR 0.35"}, "irr": irr,
       "rt": {n: [[t, d.get("LMP"), d.get("MCE"), d.get("MCC"), d.get("MCL"), d.get("MGHG")] for t, d in sorted(v.items()) if "LMP" in d] for n, v in rt.items()},
       "da": {n: [[t, p] for t, p in sorted(v.items())] for n, v in da.items()}}
json.dump(out, open("data/live.json", "w"), separators=(",", ":"))
print("wrote", {k: len(v) for k, v in out["rt"].items()}, {k: len(v) for k, v in out["da"].items()})
