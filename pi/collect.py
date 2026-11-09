#!/usr/bin/env python3
"""House dashboard collector. Every 30 s gathers Pi-hole, WireGuard, LAN, WiZ
and printer state into one JSON file that the static page polls.
Runs as root (wg show, pihole-FTL db). The Pi-hole app password is read from the file named in config (network.pihole_password_file)."""
import json, os, socket, sqlite3, subprocess, time, urllib.request, urllib.error

OUT = "/var/lib/housedash/state.json"
PW_FILE = "/etc/housedash/pihole_password"   # all three overridden from config each loop
API = "http://127.0.0.1/api"
WG_IF = "wg0"
FTL_DB = "/etc/pihole/pihole-FTL.db"
import hdconfig, notify, wizctl
WIZ = PRINTERS = HOSTS = WG_NAMES = {}   # filled from config each loop (see apply_cfg)

_sid = None

def pihole():
    global _sid
    def get(path):
        req = urllib.request.Request(API + path, headers={"X-FTL-SID": _sid or ""})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    try:
        if not _sid:
            pw = open(PW_FILE).read().strip()
            req = urllib.request.Request(API + "/auth", data=json.dumps({"password": pw}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                _sid = json.load(r)["session"]["sid"]
        s = get("/stats/summary")
        top = get("/stats/top_domains?blocked=true&count=5")
        q = s["queries"]
        return {"ok": True, "total": q["total"], "blocked": q["blocked"],
                "pct": round(q["percent_blocked"], 1), "clients": s["clients"]["active"],
                "gravity": s["gravity"]["domains_being_blocked"],
                "top_blocked": [[d["domain"], d["count"]] for d in top.get("domains", [])]}
    except urllib.error.HTTPError as e:
        if e.code == 401: _sid = None
        return {"ok": False, "err": f"http {e.code}"}
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}

def wireguard():
    try:
        out = subprocess.run(["wg", "show", WG_IF, "dump"], capture_output=True, text=True, timeout=5).stdout
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}
    peers, now = [], int(time.time())
    for i, line in enumerate(out.strip().splitlines()):
        if i == 0: continue
        f = line.split("\t")
        ip = f[3].split("/")[0]
        hs = int(f[4])
        peers.append({"name": WG_NAMES.get(ip, ip), "ip": ip, "endpoint": f[2] if f[2] != "(none)" else "",
                      "handshake_age": (now - hs) if hs else None, "rx": int(f[5]), "tx": int(f[6]),
                      "up": bool(hs) and now - hs < 180})
    return {"ok": True, "peers": peers}

KNOWN_MACS = "/var/lib/housedash/known_macs.json"   # every MAC the dashboard has seen; a new one raises the alert
_known = None

def lan():
    global _known
    try:
        db = sqlite3.connect(f"file:{FTL_DB}?mode=ro", uri=True)
        rows = db.execute("select n.ip, hw.lastQuery, hw.hwaddr, hw.macVendor, hw.firstSeen from network_addresses n join network hw on hw.id=n.network_id").fetchall()
        db.close()
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}
    now = int(time.time())
    last = {ip: lq for ip, lq, *_ in rows}
    hosts = [{"name": n, "ip": ip, "seen": (now - last[ip]) if ip in last and last[ip] else None,
              "online": ip in last and last[ip] and now - last[ip] < 600} for ip, n in HOSTS.items()]
    active = sum(1 for lq in last.values() if lq and now - lq < 900)
    # new-device detection: MACs this collector has never recorded. First run seeds the list quietly.
    if _known is None:
        try: _known = set(json.load(open(KNOWN_MACS)))
        except Exception: _known = None
    macs = {m for _, _, m, _, _ in rows if m and m != "00:00:00:00:00:00"}
    new = []
    if _known is None:
        _known = set(macs)
    else:
        for ip, lq, m, vendor, fs in rows:
            if m in macs and m not in _known:
                new.append({"ip": ip, "mac": m, "vendor": vendor or "", "first": fs})
                notify.send("new_device", "New device on the network", f"{ip}  {vendor or ''}  {m}", priority=4, tags=["rotating_light"], dedupe="new:" + m)
        _known |= macs
    try:
        with open(KNOWN_MACS + ".tmp", "w") as f: json.dump(sorted(_known), f)
        os.replace(KNOWN_MACS + ".tmp", KNOWN_MACS)
    except Exception:
        pass
    # keep the alert visible for 10 minutes after first sighting
    recent = [{"ip": ip, "mac": m, "vendor": vendor or "", "age": now - fs} for ip, lq, m, vendor, fs in rows if fs and now - fs < 600]
    # returning devices: last seen > RETURN_GAP ago on the previous pass, active now
    global _last_seen
    returned = []
    names = {ip: n for ip, n in HOSTS.items()}
    for ip, lq, m, vendor, fs in rows:
        if not m or not lq: continue
        prev = _last_seen.get(m)
        if (prev is not None and lq - prev > RETURN_GAP and now - lq < 120
                and now - _last_alert.get(m, 0) > RETURN_COOLDOWN):
            returned.append({"ip": ip, "mac": m, "name": names.get(ip) or vendor or ip, "away": lq - prev, "ts": lq})
            _last_alert[m] = now
            notify.send("device_back", f"{names.get(ip) or vendor or ip} is back", f"away {round((lq - prev) / 3600, 1)} h · {ip}", dedupe="back:" + m)
        if prev is None or lq > prev: _last_seen[m] = lq
    _returns.extend(returned)
    del _returns[:max(0, len(_returns) - 20)]
    recent_ret = [r for r in _returns if now - r["ts"] < 600]
    return {"ok": True, "hosts": hosts, "active": active, "known": len(last), "new": new, "recent_new": recent,
            "returned": recent_ret, "quiet": quiet_now(), "people": people(last, now, {m.lower(): lq for _, lq, m, _, _ in rows if m})}

# ---- who's home: phones from config "people", present if Pi-hole saw a query from them recently ----
_people_state = {}      # ip -> {"home": bool, "since": ts}
AWAY_AFTER = 1200       # phones go quiet on Wi-Fi; 20 min without DNS = left

def people(last, now, by_mac=None):
    """Each person is a phone, identified by IP or by MAC (a MAC survives DHCP changes; phones keep one
    random MAC per Wi-Fi network, so it's stable at home)."""
    out = []
    for p in PEOPLE:
        ip = (p.get("ip") or "").strip().lower()
        if ":" in ip and by_mac is not None: lq = by_mac.get(ip)          # it's a MAC
        else: lq = last.get(ip)
        home = bool(lq and now - lq < AWAY_AFTER)
        st = _people_state.get(ip)
        if st is None:
            _people_state[ip] = st = {"home": home, "since": lq or now}
        elif st["home"] != home:
            st.update(home=home, since=now)
            if home: notify.send("person_home", f"{p['name']} is home", time.strftime("%-I:%M %p"), tags=["house"], dedupe="home:" + ip, dedupe_s=600)
            else: notify.send("person_away", f"{p['name']} left", time.strftime("%-I:%M %p"), tags=["wave"], dedupe="away:" + ip, dedupe_s=600)
        out.append({"name": p["name"], "ip": ip, "home": home, "since": st["since"], "seen": (now - lq) if lq else None})
    return out

def everyone_away(people_list, minutes):
    if not people_list: return False
    now = time.time()
    return all((not p["home"]) and now - p["since"] > minutes * 60 for p in people_list)

RETURN_GAP, RETURN_COOLDOWN, QUIET = 7200, 21600, (22, 7)   # overridden from config

PEOPLE = []
def apply_cfg(c):
    global WIZ, PRINTERS, HOSTS, WG_NAMES, RETURN_GAP, RETURN_COOLDOWN, QUIET, PW_FILE, API, WG_IF, PEOPLE, AWAY_AFTER
    n = c["network"]; PW_FILE, API, WG_IF = n["pihole_password_file"], n["pihole_api"], n["wg_interface"]
    WIZ, PRINTERS, HOSTS, WG_NAMES, PEOPLE = c["lights"], c["printers"], c["hosts"], c["wg_names"], c.get("people", [])
    AWAY_AFTER = max(300, int(c["rules"].get("away_minutes", 20)) * 60)
    a = c["alerts"]
    RETURN_GAP, RETURN_COOLDOWN, QUIET = int(a["return_gap_h"] * 3600), int(a["return_cooldown_h"] * 3600), (int(a["quiet_start"]), int(a["quiet_end"]))
_last_seen = {}
_last_alert = {}
_returns = []

def quiet_now():
    h = time.localtime().tm_hour
    a, b = QUIET
    return (h >= a or h < b) if a > b else (a <= h < b)

def _sidecar(name):
    try:
        with open(f"/var/lib/housedash/{name}.json") as f: return json.load(f)
    except Exception as e:
        return {"ts": 0, "err": str(e)[:60]}

def outdoors(): return _sidecar("outdoors")
def sites(): return _sidecar("sites")          # written by sites.py (business websites)
def calendar(): return _sidecar("calendar")    # written by gcal.py (iCal feeds)
def ticker(): return _sidecar("ticker")        # written by ticker.py (UFC + hacker/esports/gaming/drone wire)
def speed(): return _sidecar("speed")          # written by speedtest.py (hourly)
def security(): return _sidecar("security")    # written by canary.py / audit.py

# ---- PiSugar: battery + mains, via pisugar-server's TCP API on :8423 ----
_power = {"plugged": None, "since": None}
def power(cfg):
    if not cfg.get("power", {}).get("on", True): return {"ok": False, "off": True}
    try:
        s = socket.create_connection(("127.0.0.1", 8423), timeout=2); out = {}
        for k in ("battery", "battery_power_plugged", "battery_charging"):
            s.sendall((f"get {k}\n").encode()); out[k] = s.recv(256).decode().split(":", 1)[-1].strip()
        s.close()
        plugged = out["battery_power_plugged"] == "true"; pct = round(float(out["battery"]))
        now = int(time.time())
        if _power["plugged"] is None: _power.update(plugged=plugged, since=now)
        elif _power["plugged"] != plugged:
            _power.update(plugged=plugged, since=now)
            if plugged: notify.send("power", "Power is back", f"mains restored · battery {pct}%", tags=["electric_plug"])
            else: notify.send("power", "POWER OUT", f"Pi on battery ({pct}%) - mains dropped", priority=5, tags=["zap"])
        return {"ok": True, "pct": pct, "plugged": plugged, "charging": out["battery_charging"] == "true", "since": _power["since"]}
    except Exception as e:
        return {"ok": False, "err": str(e)[:50]}

# ---- DNS guard: if Pi-hole's upstream is local unbound and it stops answering, fall back to Quad9 and notify ----
_dns_fail = 0
def dns_guard():
    global _dns_fail
    try:
        ups = subprocess.run(["pihole-FTL", "--config", "dns.upstreams"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {"ok": False}
    local = "127.0.0.1#5335" in ups
    if not local: return {"ok": True, "upstream": "external"}
    try:
        r = subprocess.run(["dig", "@127.0.0.1", "-p", "5335", "+short", "+time=3", "+tries=1", "example.com"], capture_output=True, text=True, timeout=8)
        good = r.returncode == 0 and r.stdout.strip() != ""
    except Exception:
        good = False
    _dns_fail = 0 if good else _dns_fail + 1
    if _dns_fail >= 3:
        subprocess.run(["pihole-FTL", "--config", "dns.upstreams", '["9.9.9.9","149.112.112.112"]'], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "restart", "unbound"], capture_output=True, timeout=20)
        notify.send("power", "DNS fallback", "unbound stopped answering - Pi-hole switched to Quad9 and unbound was restarted. Re-run unbound-setup.sh to go recursive again.", priority=4, tags=["warning"], dedupe="dnsguard", dedupe_s=3600)
        _dns_fail = 0
        return {"ok": True, "upstream": "fallback"}
    return {"ok": True, "upstream": "unbound", "healthy": good}

# ---- Bluetooth LED controllers (Zengge / Magic Lantern) - driven by blelights.py in the venv ----
BLE_PY, BLE_CLI, BLE_JSON = "/opt/housedash/venv/bin/python", "/opt/housedash/blelights.py", "/var/lib/housedash/ble.json"

def ble(cfg):
    """Configured controllers merged with the last scan (housedash-blescan.timer writes ble.json every few minutes).
    No state readback - these controllers don't report on/off - so the card shows name + in range + signal."""
    conf = cfg.get("ble_lights", {}) or {}
    try:
        d = json.load(open(BLE_JSON)); seen = {l["mac"].upper(): l for l in d.get("lights", [])}; t = d.get("t", 0)
    except Exception:
        seen, t = {}, 0
    out = []
    for mac in sorted(set(seen) | {m.upper() for m in conf}):
        c = conf.get(mac) or conf.get(mac.lower()) or {}
        if isinstance(c, str): c = {"name": c}
        l = seen.get(mac, {})
        out.append({"mac": mac, "name": c.get("name") or l.get("name") or mac, "kind": c.get("kind") or l.get("kind") or "lednetwf",
                    "rssi": l.get("rssi"), "seen": time.time() - (l.get("last") or 0) < 1200, "named": bool(c.get("name"))})   # in range = advertised within 20 min
    return {"ok": True, "lights": out, "scanned": t}

def ble_all(on, lights):
    """Fire-and-forget: one background shell walks the controllers in turn (the Pi's radio dislikes parallel connects)."""
    cmds = [f"{BLE_PY} {BLE_CLI} {'on' if on else 'off'} {l['mac']} {l['kind']}" for l in lights if l.get("seen", True)]
    if cmds:
        subprocess.Popen(["bash", "-c", " ; ".join(cmds)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---- nobody home -> lights off (config rules.lights_off_when_away) ----
_away_done = False
def away_rule(cfg, lan_state, wiz_state, ble_state=None):
    global _away_done
    r = cfg.get("rules", {})
    if not r.get("lights_off_when_away") or not lan_state.get("ok"): return
    bl = [l for l in (ble_state or {}).get("lights", []) if l.get("named")]
    if everyone_away(lan_state.get("people", []), int(r.get("away_minutes", 20))):
        if not _away_done and (wiz_state.get("on") or bl):
            wizctl.all_lights(WIZ, False); ble_all(False, bl); _away_done = True
            n = (wiz_state.get("on") or 0) + len(bl)
            notify.send("person_away", "Lights off", f"nobody home for {r.get('away_minutes', 20)} min - turned off {n} lights", tags=["bulb"])
    else:
        _away_done = False

_wiz_mac = {}   # ip -> mac, learned from getPilot replies; lets config "lights_by_mac" name bulbs that move IP
def wiz():
    out, on = [], 0
    by_mac = hdconfig.load().get("lights_by_mac", {})
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.6)
    for ip, name in WIZ.items():
        try:
            s.sendto(b'{"method":"getPilot","params":{}}', (ip, 38899))
            d = json.loads(s.recv(1024))["result"]
            mac = (d.get("mac") or "").lower(); _wiz_mac[ip] = mac
            name = by_mac.get(mac, name)
            out.append({"name": name, "ip": ip, "on": d.get("state", False), "dim": d.get("dimming", 0)})
            on += 1 if d.get("state") else 0
        except Exception:
            out.append({"name": name, "ip": ip, "on": None})
    s.close()
    return {"ok": True, "bulbs": out, "on": on}

PRINT_LOG = "/var/lib/housedash/prints.jsonl"   # one line per finished print, kept forever
_last_state = {}

def _log_print(ip, name, file, elapsed):
    with open(PRINT_LOG, "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "ip": ip, "printer": name, "file": file, "secs": elapsed}) + "\n")

def _print_counts():
    now = time.time()
    day, week = now - 86400, now - 7 * 86400
    c = {ip: {"total": 0, "week": 0, "today": 0, "hours": 0.0} for ip in PRINTERS}
    try:
        for line in open(PRINT_LOG):
            try: e = json.loads(line)
            except ValueError: continue
            x = c.get(e.get("ip"))
            if not x: continue
            x["total"] += 1; x["hours"] += e.get("secs", 0) / 3600
            if e["ts"] > week: x["week"] += 1
            if e["ts"] > day: x["today"] += 1
    except FileNotFoundError:
        pass
    for x in c.values(): x["hours"] = round(x["hours"], 1)
    return c

PRINTERS_JSON = "/var/lib/housedash/printers.json"   # written by printers.py (anycubic-lan MQTT watcher)

def printers():
    """Read what the LAN-mode watcher saw; count a print when it goes printing -> finished."""
    out = []
    counts = _print_counts()
    try:
        with open(PRINTERS_JSON) as f: seen = json.load(f).get("printers", {})
    except Exception:
        seen = {}
    for ip, name in PRINTERS.items():
        st = dict(seen.get(ip) or {"name": name, "ip": ip, "lan": False})
        st.pop("raw", None)
        st["count"] = counts[ip]
        state = (st.get("state") or "").lower()
        if st.get("progress") is not None and st["progress"] <= 1.0: st["progress"] = round(100 * st["progress"])
        for k in ("nozzle", "bed"):
            if isinstance(st.get(k), (int, float)): st[k] = round(st[k])
        if _last_state.get(ip) == "printing" and state in ("finished", "complete", "completed", "done"):
            _log_print(ip, name, st.get("file") or "", int(st.get("elapsed") or 0))
            st["count"] = _print_counts()[ip]
        if state: _last_state[ip] = state
        out.append(st)
    return {"ok": True, "printers": out}

def system():
    try:
        t = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
        up = float(open("/proc/uptime").read().split()[0])
        la = os.getloadavg()[0]
        m = dict(l.split(":") for l in open("/proc/meminfo").read().splitlines() if ":" in l)
        tot = int(m["MemTotal"].split()[0]); av = int(m["MemAvailable"].split()[0])
        return {"ok": True, "temp": round(t, 1), "uptime": int(up), "load": round(la, 2), "mem_pct": round(100 * (tot - av) / tot)}
    except Exception as e:
        return {"ok": False, "err": str(e)[:60]}

def timezone(cfg):
    """IANA zone the page should draw the clock in: config 'timezone', else the Pi's own (TVs often have theirs set wrong)."""
    tz = (cfg.get("timezone") or "").strip()
    if tz: return tz
    try:
        return open("/etc/timezone").read().strip() or None
    except OSError:
        try: return os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
        except Exception: return None

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    while True:
        cfg = hdconfig.load(); apply_cfg(cfg)
        pub = json.loads(json.dumps(cfg)); pub["notify"]["topic"] = "set" if cfg["notify"].get("topic") else ""   # topic stays off the LAN-visible state
        state = {"cfg": pub, "ts": int(time.time()), "pihole": pihole(), "wg": wireguard(), "lan": lan(),
                 "wiz": wiz(), "printers": printers(), "sys": system(), "out": outdoors(),
                 "sites": sites(), "cal": calendar(), "ticker": ticker(), "speed": speed(), "security": security(),
                 "power": power(cfg), "notify_log": notify.recent(12), "dns": dns_guard(), "ble": ble(cfg), "tz": timezone(cfg)}
        away_rule(cfg, state["lan"], state["wiz"], state["ble"])
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(state, f)
        os.replace(tmp, OUT)
        time.sleep(max(3, int(cfg["intervals"]["collect"])))

if __name__ == "__main__":
    main()
