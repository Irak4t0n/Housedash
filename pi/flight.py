"""Drone pre-flight brief + radar/tropics/marine for the house dashboard. Imported by outdoors.py.
Sources (all keyless): FAA UAS Facility Map + Class Airspace (ArcGIS public layers), FAA TFR list,
NOAA SWPC planetary Kp, Open-Meteo wind at 120 m (~400 ft), NHC CurrentStorms, NWS marine zone forecast and
surf-zone (rip current) product; sunrise/sunset computed locally (NOAA solar equations)."""
import json, math, re, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
import notify

UA = {"User-Agent": "housedash (home dashboard, github.com/Irak4t0n/housedash)", "Accept": "application/json, application/geo+json, */*"}
ARC = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"

def get(url, timeout=25):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)

# ---------- sun ----------
def sun_times(lat, lon, day=None):
    """Sunrise, sunset, civil dawn/dusk as local datetimes (NOAA simplified algorithm, +-2 min)."""
    day = day or datetime.now().astimezone()
    tz = day.tzinfo
    def calc(zenith, rising):
        N = day.timetuple().tm_yday
        lngHour = lon / 15
        t = N + ((6 if rising else 18) - lngHour) / 24
        M = 0.9856 * t - 3.289
        L = (M + 1.916 * math.sin(math.radians(M)) + 0.020 * math.sin(math.radians(2 * M)) + 282.634) % 360
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
        RA = (RA + (math.floor(L / 90) * 90 - math.floor(RA / 90) * 90)) / 15
        sinDec = 0.39782 * math.sin(math.radians(L)); cosDec = math.cos(math.asin(sinDec))
        cosH = (math.cos(math.radians(zenith)) - sinDec * math.sin(math.radians(lat))) / (cosDec * math.cos(math.radians(lat)))
        if cosH > 1 or cosH < -1: return None
        H = (360 - math.degrees(math.acos(cosH))) if rising else math.degrees(math.acos(cosH))
        T = (H / 15 + RA - 0.06571 * t - 6.622 - lngHour) % 24
        base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=T)
        return base.astimezone(tz)
    return {"sunrise": calc(90.833, True), "sunset": calc(90.833, False), "dawn": calc(96, True), "dusk": calc(96, False)}

def hhmm(dt): return dt.strftime("%-I:%M %p").lower() if dt else "--"

# ---------- FAA ----------
def arc_point(service, lat, lon, fields="*"):
    q = urllib.parse.urlencode({"where": "1=1", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": "false", "f": "json"})
    return get(f"{ARC}{service}/FeatureServer/0/query?{q}").get("features", [])

def laanc(lat, lon):
    """UAS Facility Map grid ceiling at the point (LAANC auto-approval altitude), or None = no grid here."""
    feats = arc_point("FAA_UAS_FacilityMap_Data", lat, lon, "CEILING,APT1_NAME,APT1_FAA,APT1_LAANC,UASFM_TYPE")
    if not feats: return {"grid": False, "ceiling": None}
    a = feats[0]["attributes"]
    ceil = a.get("CEILING")
    return {"grid": True, "ceiling": int(ceil) if ceil is not None else None, "airport": a.get("APT1_NAME") or a.get("APT1_FAA"), "laanc": a.get("APT1_LAANC")}

def airspace_class(lat, lon):
    feats = arc_point("Class_Airspace", lat, lon, "CLASS,NAME,LOWER_VAL,UPPER_VAL,LOWER_UOM")
    best = None
    for f in feats:
        a = f["attributes"]
        if (a.get("LOWER_VAL") in (0, "0", None, -9998)) and (best is None or (a.get("CLASS") or "Z") < (best.get("CLASS") or "Z")):
            best = a
    return (best or {}).get("CLASS")

_tfr_geo = {}   # notam id -> (lat, lon) or None, cached (the detail XML is slow)

def _dms(s):
    m = re.match(r"(\d+(?:\.\d+)?)([NSEW])", s.strip())
    if not m: return None
    v = float(m.group(1)); return -v if m.group(2) in "SW" else v

def tfr_center(nid):
    if nid in _tfr_geo: return _tfr_geo[nid]
    try:
        url = f"https://tfr.faa.gov/download/detail_{nid.replace('/', '_')}.xml"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r: x = r.read().decode("utf-8", "ignore")
        lats = [_dms(v) for v in re.findall(r"<geoLat>([^<]+)", x)]; lons = [_dms(v) for v in re.findall(r"<geoLong>([^<]+)", x)]
        lats = [v for v in lats if v is not None]; lons = [v for v in lons if v is not None]
        _tfr_geo[nid] = (sum(lats) / len(lats), sum(lons) / len(lons)) if lats and lons else None
    except Exception:
        _tfr_geo[nid] = None
    return _tfr_geo[nid]

def nm(lat1, lon1, lat2, lon2):
    p = math.pi / 180; a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 3440.1 * 2 * math.asin(math.sqrt(a))

def tfrs(state_codes, lat, lon, radius_nm):
    """Active TFRs within radius_nm of the point. The FAA list has no coordinates, so candidates are
    narrowed by state first and each one's detail XML gives the area centre."""
    out = []
    for t in get("https://tfr.faa.gov/tfrapi/exportTfrList"):
        if t.get("state") not in state_codes: continue
        c = tfr_center(t.get("notam_id", ""))
        dist = round(nm(lat, lon, c[0], c[1])) if c else None
        if dist is None or dist <= radius_nm:
            out.append({"id": t.get("notam_id"), "type": t.get("type"), "desc": (t.get("description") or "")[:110], "state": t.get("state"), "nm": dist})
    out.sort(key=lambda x: (x["nm"] is None, x["nm"] or 0))
    return out

# ---------- space weather / wind aloft ----------
def kp():
    rows = get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    last = rows[-1]
    return float(last["Kp"]) if isinstance(last, dict) else float(last[1])

def wind_aloft(lat, lon):
    d = get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=wind_speed_120m,wind_gusts_10m,precipitation_probability,wind_direction_120m&wind_speed_unit=mph&forecast_days=1&timezone=auto")
    h = d["hourly"]; now = datetime.now().strftime("%Y-%m-%dT%H:00")
    i = h["time"].index(now) if now in h["time"] else 0
    nxt = list(range(i, min(i + 6, len(h["time"]))))
    return {"w120": round(h["wind_speed_120m"][i]), "gust": round(h["wind_gusts_10m"][i]), "dir": round(h["wind_direction_120m"][i]),
            "rain": h["precipitation_probability"][i], "w120_max6h": round(max(h["wind_speed_120m"][j] for j in nxt)),
            "hours": [{"t": h["time"][j][11:16], "w": round(h["wind_speed_120m"][j]), "g": round(h["wind_gusts_10m"][j])} for j in nxt]}

STATES_NEAR = {"AL": ["AL", "FL", "MS"], "FL": ["FL", "AL", "GA"], "MS": ["MS", "AL", "LA"], "LA": ["LA", "MS", "TX"], "TX": ["TX", "LA", "OK", "NM"], "GA": ["GA", "FL", "AL", "SC"]}

def brief(cfg, wx):
    f = cfg["flight"]; w = cfg["weather"]; lat, lon = float(w["lat"]), float(w["lon"])
    out = {"ok": True}
    sun = sun_times(lat, lon)
    now = datetime.now().astimezone()
    out["sun"] = {k: hhmm(v) for k, v in sun.items()}
    out["golden"] = {"am": f"{hhmm(sun['sunrise'])}–{hhmm(sun['sunrise'] + timedelta(hours=1)) if sun['sunrise'] else '--'}",
                     "pm": f"{hhmm(sun['sunset'] - timedelta(hours=1)) if sun['sunset'] else '--'}–{hhmm(sun['sunset'])}"}
    daylight = bool(sun["dawn"] and sun["dusk"] and sun["dawn"] - timedelta(minutes=30) <= now <= sun["dusk"] + timedelta(minutes=30))
    out["daylight"] = daylight
    nogo = []
    try: out["laanc"] = laanc(lat, lon)
    except Exception as e: out["laanc"] = {"err": str(e)[:50]}
    try: out["class"] = airspace_class(lat, lon) or "G"
    except Exception as e: out["class"] = "?"
    try:
        out["tfrs"] = tfrs(STATES_NEAR.get(w.get("state", "AL"), ["AL", "FL", "MS"]), lat, lon, int(f.get("tfr_radius_nm", 40)))
        if out["tfrs"]: nogo.append(f"TFR {out['tfrs'][0]['nm']} nm away")
    except Exception as e: out["tfrs"] = []; out["tfr_err"] = str(e)[:50]
    try:
        out["kp"] = kp()
        if out["kp"] >= f["max_kp"]: nogo.append(f"Kp {out['kp']:.1f} (GPS)")
    except Exception as e: out["kp"] = None
    try:
        wa = wind_aloft(lat, lon); out["aloft"] = wa
        if wa["w120"] > f["max_wind_mph"]: nogo.append(f"{wa['w120']} mph at 400 ft")
        elif wa["gust"] > f["max_wind_mph"] + 6: nogo.append(f"gusts {wa['gust']} mph")
        if wa["rain"] >= 50: nogo.append(f"rain {wa['rain']}%")
    except Exception as e: out["aloft"] = None; out["aloft_err"] = str(e)[:50]
    if wx and wx.get("ok") and wx.get("alerts"): nogo.append(wx["alerts"][0]["event"])
    if not daylight: nogo.append("night (Part 107 needs anti-collision lights)")
    out["nogo"] = nogo
    out["verdict"] = "GO" if not nogo else ("CAUTION" if len(nogo) == 1 and "night" in nogo[0] else "NO-GO")
    return out

# ---------- tropics ----------
_storms_seen = set()
def tropics():
    d = get("https://www.nhc.noaa.gov/CurrentStorms.json")
    storms = []
    for s in d.get("activeStorms", []):
        basin = (s.get("binNumber") or "")[:2]
        storms.append({"name": s.get("name"), "class": s.get("classification"), "kt": s.get("intensity"), "mb": s.get("pressure"),
                       "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"), "move": f"{s.get('movementDir', '')}° {s.get('movementSpeed', '')} kt".strip(),
                       "basin": {"AT": "Atlantic", "EP": "E Pacific", "CP": "C Pacific"}.get(basin, basin), "atlantic": basin == "AT"})
        key = s.get("id")
        if basin == "AT" and key and key not in _storms_seen:
            if _storms_seen: notify.send("storm", f"{s.get('classification')} {s.get('name')} in the Atlantic", f"{s.get('intensity')} kt · {s.get('pressure')} mb · moving {s.get('movementDir')}° at {s.get('movementSpeed')} kt", priority=4, tags=["cyclone"], dedupe="storm:" + key, dedupe_s=86400)
            _storms_seen.add(key)
    if not _storms_seen: _storms_seen.add("_seeded")
    return {"ok": True, "storms": storms, "atlantic": [s for s in storms if s["atlantic"]]}

# ---------- marine ----------
def marine(zone, office):
    out = {"ok": True}
    try:
        z = get(f"https://api.weather.gov/zones/forecast/{zone}/forecast")
        periods = z.get("periods") or z.get("properties", {}).get("periods", [])
        out["periods"] = [{"name": p["name"], "text": p["detailedForecast"][:220]} for p in periods[:2]]
    except Exception as e: out["err"] = str(e)[:60]
    try:
        g = get(f"https://api.weather.gov/products/types/SRF/locations/{office}").get("@graph", [])
        if g:
            txt = get(g[0]["@id"]).get("productText", "")
            m = re.search(r"Rip Current Risk\*?[.\s]*([A-Za-z]+)", txt, re.I)
            out["rip"] = m.group(1).capitalize() if m else None
            m2 = re.search(r"\nSurf\.{3,}\s*([^\n]{0,60})", txt)
            out["surf"] = m2.group(1).strip(" .") if m2 else None
            m3 = re.search(r"Water Temperature\.{3,}\s*([^\n]{0,30})", txt)
            out["water"] = m3.group(1).strip(" .") if m3 else None
    except Exception as e: out["srf_err"] = str(e)[:60]
    return out
