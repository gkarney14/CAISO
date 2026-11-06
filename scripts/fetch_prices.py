"""Fetch CAISO OASIS DA hourly and RT 15-min (RTPD) LMPs for the cohort node and NP15 hub.
Writes data/prices.json. Usage: python scripts/fetch_prices.py [days_back]"""
import io, json, sys, time, zipfile, urllib.request, datetime as dt, csv
from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los_Angeles")
NODES = {"SCARLT_7_N010": "node", "TH_NP15_GEN-APND": "np15"}   # N010 = Scarlet cohort (all cohort pnodes clear within ~$0.15)
QUERIES = [("PRC_LMP", "DAM", 12, "da"), ("PRC_RTPD_LMP", "RTPD", 3, "rt")]
days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
end = dt.datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=2)
start = end - dt.timedelta(days=days + 2)
def fetch(q, m, v, s, e):
    u = (f"https://oasis.caiso.com/oasisapi/SingleZip?resultformat=6&queryname={q}&version={v}&market_run_id={m}"
         f"&node={','.join(NODES)}&startdatetime={s.astimezone(dt.timezone.utc):%Y%m%dT%H:%M-0000}&enddatetime={e.astimezone(dt.timezone.utc):%Y%m%dT%H:%M-0000}")
    for attempt in range(5):
        try:
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=300).read()))
            f = z.namelist()[0]
            if not f.endswith(".csv"):
                txt = z.read(f).decode(errors="ignore")
                if "No data returned" in txt: return []
                raise RuntimeError(txt[:200])
            return list(csv.DictReader(io.TextIOWrapper(z.open(f))))
        except Exception as ex:
            print("  retry", attempt, ex, flush=True); time.sleep(8 * (attempt + 1))
    return []
out = {k: {"da": {}, "rt": {}} for k in NODES.values()}
for q, m, v, key in QUERIES:
    s = start
    while s < end:
        e = min(s + dt.timedelta(days=30), end)
        rows = fetch(q, m, v, s, e)
        n = 0
        for r in rows:
            if r["LMP_TYPE"] != "LMP" or r["NODE"] not in NODES: continue
            t = dt.datetime.fromisoformat(r["INTERVALSTARTTIME_GMT"]).astimezone(PT)
            val = float(r.get("MW") or r.get("PRC") or r.get("VALUE"))
            out[NODES[r["NODE"]]][key][t.isoformat()] = round(val, 2); n += 1
        print(q, s.date(), e.date(), n, flush=True)
        s = e; time.sleep(6)
# compact: per series, sorted list of [epoch_seconds, price]
def pack(d): return [[int(dt.datetime.fromisoformat(t).timestamp()), p] for t, p in sorted(d.items(), key=lambda kv: dt.datetime.fromisoformat(kv[0]))]
res = {"generated": dt.datetime.now(PT).isoformat(timespec="minutes"), "tz": "America/Los_Angeles",
       "nodes": {"node": "SCARLT_7_N010 (Scarlet/Sonrisa cohort)", "np15": "TH_NP15_GEN-APND"},
       "series": {k: {m: pack(v[m]) for m in v} for k, v in out.items()}}
json.dump(res, open("data/prices.json", "w"), separators=(",", ":"))
print("wrote", {k: {m: len(v[m]) for m in v} for k, v in out.items()})
