#!/usr/bin/env python3
"""Weather, tide, water temperature, fishing outlook and internet health for the house
dashboard. Slow sources, so this runs on its own (every 10 min) and writes
/var/lib/housedash/outdoors.json, which collect.py folds into state.json.
Sources: api.weather.gov (NWS, no key), api.tidesandcurrents.noaa.gov (NOAA CO-OPS)."""
import json, os, socket, subprocess, time, urllib.request
from datetime import datetime, timedelta, timezone

OUT = "/var/lib/housedash/outdoors.json"
UA = {"User-Agent": "housedash (home dashboard, github.com/Irak4t0n/housedash)", "Accept": "application/geo+json,application/json"}
import hdconfig, flight
CFG = hdconfig.load()
_grid = {"key": None, "hourly": None, "daily": None}

def nws_urls():
    # resolve lat/lon -> NWS gridpoint once per location change (api.weather.gov/points)
    w = CFG["weather"]; key = (round(float(w["lat"]), 4), round(float(w["lon"]), 4))
    if _grid["key"] != key:
        p = get(f"https://api.weather.gov/points/{key[0]},{key[1]}")["properties"]
        _grid.update(key=key, hourly=p["forecastHourly"], daily=p["forecast"])
    return _grid["hourly"], _grid["daily"], f"https://api.weather.gov/alerts/active?point={key[0]},{key[1]}"
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

def get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)

def weather():
    try:
        NWS_HOURLY, NWS_DAILY, NWS_ALERTS = nws_urls()
        h = get(NWS_HOURLY)["properties"]["periods"]
        d = get(NWS_DAILY)["properties"]["periods"]
        now = h[0]
        def wind(p):
            return int(p["windSpeed"].split()[0]) if p.get("windSpeed") else None
        hours = [{"t": p["startTime"][11:16], "temp": p["temperature"], "wind": wind(p), "dir": p.get("windDirection"),
                  "rain": (p.get("probabilityOfPrecipitation") or {}).get("value") or 0, "short": p["shortForecast"]} for p in h[:12]]
        gust_max = max((wind(p) or 0) for p in h[:12])
        days = [{"name": p["name"], "temp": p["temperature"], "short": p["shortForecast"], "wind": p.get("windSpeed"),
                 "rain": (p.get("probabilityOfPrecipitation") or {}).get("value") or 0, "day": p["isDaytime"]} for p in d[:6]]
        alerts = [{"event": a["properties"]["event"], "headline": a["properties"].get("headline", "")}
                  for a in get(NWS_ALERTS).get("features", [])]
        # a drone-pilot verdict from the next 6 h: Part 107 is fine to 30+ kt but footage isn't
        w6 = [wind(p) or 0 for p in h[:6]]
        fw = CFG["weather"]; fly = "good" if max(w6) <= fw["fly_good_mph"] else "ok" if max(w6) <= fw["fly_ok_mph"] else "windy"
        return {"ok": True, "now": {"temp": now["temperature"], "short": now["shortForecast"], "wind": wind(now), "dir": now.get("windDirection"),
                                    "humidity": (now.get("relativeHumidity") or {}).get("value")},
                "hours": hours, "days": days, "alerts": alerts, "fly": fly, "wind_max_12h": gust_max}
    except Exception as e:
        return {"ok": False, "err": str(e)[:80]}

def tides():
    try:
        today = datetime.now().strftime("%Y%m%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        q = f"{COOPS}?product=predictions&datum=MLLW&station={CFG['tides']['station']}&time_zone=lst_ldt&units=english&interval=hilo&format=json&begin_date={today}&end_date={tomorrow}"
        preds = get(q)["predictions"]
        now = datetime.now()
        ev = [{"t": p["t"], "type": "high" if p["type"] == "H" else "low", "ft": round(float(p["v"]), 1)} for p in preds]
        nxt = [e for e in ev if datetime.strptime(e["t"], "%Y-%m-%d %H:%M") > now][:4]
        wt = None
        try:
            w = get(f"{COOPS}?product=water_temperature&station={CFG['tides']['water_temp_station']}&date=latest&time_zone=lst_ldt&units=english&format=json")
            wt = round(float(w["data"][0]["v"]))
        except Exception:
            pass
        return {"ok": True, "station": CFG["tides"]["label"], "next": nxt, "water_temp": wt}
    except Exception as e:
        return {"ok": False, "err": str(e)[:80]}

# What is usually biting on the Alabama Gulf Coast, by month. Inshore (Mobile Bay, back bays,
# the ICWW) and surf/pier, with the water temperature nudging the list. Written from the
# Alabama Marine Resources seasonal patterns; not a live report.
BITING = {
    1:  (["Sheepshead", "Redfish (deep holes)", "Speckled trout (rivers, deep)"], ["Whiting", "Black drum"]),
    2:  (["Sheepshead (spawn, pilings)", "Redfish", "Black drum"], ["Whiting", "Pompano starting"]),
    3:  (["Sheepshead", "Speckled trout (warming flats)", "Redfish"], ["Pompano", "Whiting", "Spanish mackerel first run"]),
    4:  (["Speckled trout", "Redfish", "Flounder moving in"], ["Pompano (prime)", "Spanish mackerel", "Bluefish"]),
    5:  (["Speckled trout (topwater early)", "Redfish", "Flounder"], ["Pompano", "Spanish mackerel", "King mackerel off the pier", "Cobia run"]),
    6:  (["Speckled trout (night, lights)", "Redfish", "Flounder", "Tripletail (crab traps)"], ["Spanish & king mackerel", "Ladyfish", "Jack crevalle", "Red snapper season offshore"]),
    7:  (["Speckled trout (early/late)", "Redfish", "Tripletail", "Mangrove snapper (structure)"], ["Spanish & king mackerel", "Tarpon off the beach", "Ladyfish"]),
    8:  (["Redfish", "Speckled trout (deep, early)", "Mangrove snapper"], ["Kings & Spanish", "Tarpon", "Blue runner"]),
    9:  (["Bull redfish start", "Speckled trout picking up", "Flounder staging"], ["Spanish mackerel", "Pompano returning", "Bull reds in the passes"]),
    10: (["Flounder run (prime)", "Bull redfish", "Speckled trout"], ["Bull reds (surf/pier)", "Pompano", "Spanish mackerel"]),
    11: (["Flounder (late run)", "Speckled trout", "Redfish", "Sheepshead starting"], ["Pompano", "Whiting", "Bull reds"]),
    12: (["Speckled trout (rivers)", "Redfish", "Sheepshead"], ["Whiting", "Black drum"]),
}

def fishing(wx, td):
    m = datetime.now().month
    inshore, surf = BITING[m]
    tips = []
    wt = td.get("water_temp") if td.get("ok") else None
    if wt is not None:
        if wt < 55: tips.append("Cold water: fish slow and deep, soft plastics on the bottom.")
        elif wt < 65: tips.append("Cool water: trout and reds on the deeper edges, slow retrieves.")
        elif wt < 78: tips.append("Prime water temp: topwater early, live shrimp under a cork all day.")
        else: tips.append("Warm water: fish first light, last light, and lighted docks at night.")
    if wx.get("ok"):
        w = wx["now"].get("wind") or 0
        if w >= 18: tips.append("Too windy for the surf; fish the lee shore of the back bays.")
        elif w <= 8: tips.append("Light wind: good day for the pier or wading the flats.")
    if td.get("ok") and td["next"]:
        n = td["next"][0]
        tips.append(f"Next {n['type']} tide {n['t'][11:]}; the two hours around it are the window.")
    return {"inshore": inshore, "surf": surf, "tips": tips}

_ipcache = {"t": 0, "public_ip": None, "ddns_ip": None}

def internet():
    out = {"ok": True}
    # latency to two anycast resolvers — every pass (fast: 3 pings, 0.2 s apart)
    for name, ip in (("cloudflare", "1.1.1.1"), ("google", "8.8.8.8")):
        try:
            r = subprocess.run(["ping", "-c", "3", "-i", "0.2", "-W", "1.5", ip], capture_output=True, text=True, timeout=8).stdout
            loss = int(r.split("% packet loss")[0].rsplit(" ", 1)[1]) if "packet loss" in r else 100
            avg = float(r.split("min/avg/max")[1].split("=")[1].split("/")[1]) if "min/avg/max" in r else None
            out[name] = {"ms": round(avg) if avg is not None else None, "loss": loss}
        except Exception:
            out[name] = {"ms": None, "loss": 100}
    # public IP / DDNS check only once a minute (be polite to ipify)
    if time.time() - _ipcache["t"] > 60:
        try: _ipcache["public_ip"] = urllib.request.urlopen("https://api.ipify.org", timeout=8).read().decode().strip()
        except Exception: _ipcache["public_ip"] = None
        try: _ipcache["ddns_ip"] = socket.gethostbyname(CFG["network"]["ddns_host"])
        except Exception: _ipcache["ddns_ip"] = None
        _ipcache["t"] = time.time()
    out["public_ip"], out["ddns_ip"] = _ipcache["public_ip"], _ipcache["ddns_ip"]
    out["ddns_ok"] = bool(out["public_ip"]) and out["public_ip"] == out["ddns_ip"]
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    global CFG
    wx = td = fi = None; slow_t = 0
    while True:
        CFG = hdconfig.load(); FAST, SLOW = CFG["intervals"]["internet"], CFG["intervals"]["outdoors"]
        if time.time() - slow_t >= SLOW or wx is None:
            wx, td = weather(), tides(); fi = fishing(wx, td); slow_t = time.time()
            try: fb = flight.brief(CFG, wx) if CFG["flight"].get("on", True) else {"ok": False, "off": True}
            except Exception as e: fb = {"ok": False, "err": str(e)[:60]}
            try: tr = flight.tropics()
            except Exception as e: tr = {"ok": False, "err": str(e)[:60]}
            try: ma = flight.marine(CFG["marine"]["zone"], CFG["marine"]["surf_office"])
            except Exception as e: ma = {"ok": False, "err": str(e)[:60]}
            radar = {"img": f"https://radar.weather.gov/ridge/standard/{CFG['radar']['station']}_loop.gif", "station": CFG["radar"]["station"]}
        data = {"ts": int(time.time()), "weather": wx, "tides": td, "fishing": fi, "internet": internet(),
                "flight": fb, "tropics": tr, "marine": ma, "radar": radar}
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, OUT)
        time.sleep(max(3, int(FAST)))

if __name__ == "__main__":
    main()
