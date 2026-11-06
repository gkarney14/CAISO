"""Fetch live CAISO data for the public dashboard: RT 5-min LMP + components (yesterday 00:00 PT to now)
for the cohort pnodes and hubs, and DA hourly LMP (today + tomorrow) for the node and NP15.
Writes data/live.json."""
import io, json, time, zipfile, urllib.request, csv, datetime as dt
from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los_Angeles")
RT_NODES = {"SCARLT_7_N011": "scarlet", "SCARLT_7_ND001": "sonrisa", "TH_NP15_GEN-APND": "np15",
            "TH_ZP26_GEN-APND": "zp26", "TH_SP15_GEN-APND": "sp15"}
DA_NODES = {"SCARLT_7_N011": "scarlet", "TH_NP15_GEN-APND": "np15"}
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
for r in get("PRC_INTVL_LMP", 3, "RTM", RT_NODES, today - dt.timedelta(days=1), now + dt.timedelta(minutes=10)):
    n = RT_NODES.get(r["NODE"]); typ = r["LMP_TYPE"]
    if not n or typ not in ("LMP", "MCE", "MCC", "MCL", "MGHG"): continue
    t = int(dt.datetime.fromisoformat(r["INTERVALSTARTTIME_GMT"]).timestamp())
    rt.setdefault(n, {}).setdefault(t, {})[typ] = round(val(r), 2)
time.sleep(6)
da = {}
for r in get("PRC_LMP", 12, "DAM", DA_NODES, today, today + dt.timedelta(days=2)):
    if r["LMP_TYPE"] != "LMP" or r["NODE"] not in DA_NODES: continue
    t = int(dt.datetime.fromisoformat(r["INTERVALSTARTTIME_GMT"]).timestamp())
    da.setdefault(DA_NODES[r["NODE"]], {})[t] = round(val(r), 2)
out = {"generated": int(time.time()),
       "rt": {n: [[t, d.get("LMP"), d.get("MCE"), d.get("MCC"), d.get("MCL"), d.get("MGHG")] for t, d in sorted(v.items()) if "LMP" in d] for n, v in rt.items()},
       "da": {n: [[t, p] for t, p in sorted(v.items())] for n, v in da.items()}}
json.dump(out, open("data/live.json", "w"), separators=(",", ":"))
print("wrote", {k: len(v) for k, v in out["rt"].items()}, {k: len(v) for k, v in out["da"].items()})
