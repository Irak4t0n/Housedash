#!/usr/bin/env python3
"""House dashboard setup — http://<pi>:8089 (LAN + VPN). Edits /etc/housedash/config.json
(everything the dashboard shows and how often) plus the two write-only secrets (calendar feeds, WordPress
app password). Runs as root so it can write /etc/housedash and restart collectors. Secrets never echo back."""
import base64, copy, html, json, os, re, secrets, socket, subprocess, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hdconfig, notify, wizctl

CAL = "/etc/housedash/calendars.txt"
ENV = "/etc/housedash/sites.env"
E = html.escape

CSS = """body{font:16px system-ui;background:#0b0f14;color:#e6edf3;margin:0;padding:16px;max-width:860px;margin:auto}
h1{font-size:22px;margin:8px 0 2px}h2{font-size:13px;color:#8b98a8;text-transform:uppercase;letter-spacing:.12em;margin:26px 0 8px}
.card{background:#121923;border:1px solid #1f2a38;border-radius:12px;padding:14px 16px;margin-bottom:12px}
input,select,textarea{box-sizing:border-box;padding:9px 10px;border-radius:8px;border:1px solid #1f2a38;background:#0b0f14;color:#fff;font-size:15px}
input[type=checkbox]{width:20px;height:20px;vertical-align:middle;accent-color:#4cc2ff}input[type=color]{padding:0;width:40px;height:34px}
label{display:block;margin:8px 0 2px;color:#8b98a8;font-size:13px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}.row>div{flex:1;min-width:120px}
button{padding:10px 16px;border:0;border-radius:8px;background:#4cc2ff;color:#000;font-weight:600;font-size:15px;cursor:pointer;margin-top:10px}
button.x{background:#3a1515;color:#fff;padding:6px 10px;font-size:13px;margin:0}
table{width:100%;border-collapse:collapse;margin-top:6px}td,th{padding:5px 4px;border-bottom:1px solid #1f2a38;text-align:left;font-size:14px}th{color:#8b98a8;font-weight:500}
td input[type=text],td input[type=number],td input[type=url]{width:100%}td.n{width:60px}td.c{width:34px;text-align:center}td.k{width:150px}
.ok{color:#3ddc84}.bad{color:#ff5c5c}.dim{color:#8b98a8;font-size:13px}.msg{padding:10px 14px;border-radius:10px;margin:10px 0;background:#121923;border:1px solid #1f2a38}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px}nav a{color:#4cc2ff;text-decoration:none;font-size:13px;background:#121923;border:1px solid #1f2a38;padding:5px 9px;border-radius:20px}
textarea{width:100%;height:320px;font:13px ui-monospace,monospace}
ol.help{padding-left:20px;font-size:13px;color:#8b98a8;line-height:1.5;margin:6px 0}"""

SECTIONS = ["general", "cards", "notify", "alerts", "people", "wol", "wire", "weather", "flight", "printers", "lights", "hosts", "vpn", "sites", "security", "calendars", "wordpress", "json"]

def restart(*units): subprocess.run(["systemctl", "restart", *units])

def cals():
    out = []
    if os.path.exists(CAL):
        for line in open(CAL):
            line = line.strip()
            if line and not line.startswith("#"): out.append([x.strip() for x in line.split("|")][0])
    return out

def write_cals(keep=None, add=None):
    lines = [l for l in (open(CAL).read().splitlines() if os.path.exists(CAL) else []) if l.strip() and not l.startswith("#")]
    if keep is not None: lines = [l for l in lines if l.split("|")[0].strip() in keep]
    if add: lines.append(add)
    os.makedirs("/etc/housedash", exist_ok=True)
    with open(CAL, "w") as f: f.write("# Name | #color | iCal URL\n" + "\n".join(lines) + "\n")
    os.chmod(CAL, 0o600); restart("housedash-calendar")

def test_ics(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "housedash/1"}), timeout=20) as r: head = r.read(2000)
    if b"BEGIN:VCALENDAR" not in head: raise ValueError("that link isn't an iCal feed (should end in .ics)")

def test_wp(site_url, user, pw):
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(site_url + "wp-json/wp/v2/users/me", headers={"Authorization": "Basic " + tok, "User-Agent": "housedash/1"})
    with urllib.request.urlopen(req, timeout=20) as r: return json.load(r).get("name")

# ---------- form builders ----------
def inp(name, val, typ="text", step=None, w=None):
    extra = f' step="{step}"' if step else ""
    return f'<input type="{typ}" name="{name}" value="{E(str(val))}"{extra}>'
def chk(name, val): return f'<input type="checkbox" name="{name}"{" checked" if val else ""}>'
def field(label, inner): return f"<div><label>{label}</label>{inner}</div>"

def dict_table(c, key, k_label, v_label, note):
    rows = "".join(f"<tr><td class=k>{inp(f'k{i}', k)}</td><td>{inp(f'v{i}', v)}</td></tr>" for i, (k, v) in enumerate(c[key].items()))
    return f"<p class=dim>{note} Clear a name to remove that row.</p><table><tr><th>{k_label}</th><th>{v_label}</th></tr>{rows}<tr><td class=k>{inp('newk','')}</td><td>{inp('newv','')}</td></tr></table><button>Save</button>"

def section_html(c, sec):
    if sec == "general":
        iv = c["intervals"]
        return (f"<div class=row>{field('House name (header)', inp('title', c['title']))}{field('Grid columns', inp('cols', c['grid']['columns'], 'number'))}{field('Grid rows', inp('rows', c['grid']['rows'], 'number'))}</div>"
                f"<h2>Refresh rates (seconds)</h2><div class=row>{field('Page poll', inp('page', iv['page'], 'number'))}{field('Collector (Pi-hole, VPN, devices, lights)', inp('collect', iv['collect'], 'number'))}{field('Internet ping', inp('internet', iv['internet'], 'number'))}"
                f"{field('Websites', inp('sites', iv['sites'], 'number'))}{field('Weather / tide', inp('outdoors', iv['outdoors'], 'number'))}{field('Wire feeds', inp('ticker', iv['ticker'], 'number'))}</div>"
                f"<div class=row>{field('Public IP / DDNS hostname to compare', inp('ddns', c['network']['ddns_host']))}</div><button>Save</button>")
    if sec == "cards":
        rows = "".join(f"<tr><td class=c>{chk(f'on{i}', x.get('on', True))}</td><td>{inp(f'label{i}', x.get('label', ''))}</td><td class=n>{inp(f'pos{i}', i + 1, 'number')}</td><td class=c>{chk(f'tall{i}', x.get('tall'))}</td><td class=dim>{x['id']}</td></tr>" for i, x in enumerate(c["cards"]))
        return f"<p class=dim>Tick to show, type a position to reorder, 'tall' spans two rows. {c['grid']['columns']}×{c['grid']['rows']} grid = {c['grid']['columns'] * c['grid']['rows']} slots (tall cards use two).</p><table><tr><th>show</th><th>label</th><th>pos</th><th>tall</th><th>id</th></tr>{rows}</table><button>Save</button>"
    if sec == "notify":
        n = c["notify"]; ev = n["events"]
        labels = {"new_device": "new device on the network", "device_back": "known device back after hours away", "person_home": "someone comes home", "person_away": "someone leaves / lights-off rule fired",
                  "print_done": "print finished", "print_error": "printer error or paused", "site_down": "website down / back up", "site_changed": "website home page changed",
                  "cert_expiring": "TLS cert < 14 days", "domain_expiring": "domain registration < 30 days", "power": "mains power lost / restored (PiSugar)", "canary": "honeypot touched",
                  "audit": "nightly port scan found changes", "storm": "new tropical storm / hurricane in the Gulf", "test": "test button"}
        boxes = "".join(f"<p style='margin:4px 0'>{chk('ev_' + k, ev.get(k, True))} {E(v)}</p>" for k, v in labels.items())
        sub = f"{n['server'].rstrip('/')}/{n['topic']}"
        return (f"<p>{chk('on', n['on'])} send push notifications &nbsp;&nbsp; {chk('quiet', n['quiet_respect'])} deliver silently during quiet hours</p>"
                f"<div class=row>{field('ntfy server', inp('server', n['server']))}{field('Topic (keep it private - anyone with it can post to your phone)', inp('topic', n['topic']))}</div>"
                f"<ol class=help><li>Install the <b>ntfy</b> app (Android / iOS) or open <a style=color:#4cc2ff href='{E(sub)}' target=_blank>{E(sub)}</a> in a browser.</li><li>Subscribe to topic <b>{E(n['topic'])}</b> on server {E(n['server'])}. That's it - phones on any network get them.</li></ol>"
                f"<button formaction=/api/notify/test formmethod=post>Send a test</button> <button>Save</button><h2>Events</h2>{boxes}<button>Save</button>")
    if sec == "people":
        rows = "".join(f"<tr><td class=k>{inp(f'k{i}', p['name'])}</td><td>{inp(f'v{i}', p['ip'])}</td></tr>" for i, p in enumerate(c["people"]))
        r = c["rules"]
        return (f"<p class=dim>Phones, by IP (give them DHCP reservations in your router). Home = Pi-hole saw a DNS query from it in the last N minutes. Clear a name to remove.</p>"
                f"<table><tr><th>name</th><th>phone IP</th></tr>{rows}<tr><td class=k>{inp('newk', '')}</td><td>{inp('newv', '')}</td></tr></table>"
                f"<div class=row>{field('Away after (minutes without DNS)', inp('away', r['away_minutes'], 'number'))}</div>"
                f"<p>{chk('lightsoff', r['lights_off_when_away'])} turn all lights off when everyone has been away that long</p><button>Save</button>")
    if sec == "wol":
        rows = "".join(f"<tr><td class=k>{inp(f'name{i}', w['name'])}</td><td class=k>{inp(f'mac{i}', w['mac'])}</td><td>{inp(f'ip{i}', w.get('ip', ''))}</td><td class=c><input type=checkbox name='del{i}'></td></tr>" for i, w in enumerate(c["wol"]))
        return (f"<p class=dim>Wake-on-LAN targets: a <b>Wake</b> button appears on the dashboard's phone/PC view and at <code>POST /api/wake?name=…</code> on this port. The PC needs 'Wake on magic packet' enabled on its NIC and in BIOS. Last column removes.</p>"
                f"<table><tr><th>name</th><th>MAC</th><th>IP (to show if it's up)</th><th>✕</th></tr>{rows}<tr><td class=k>{inp('name_new', '')}</td><td class=k>{inp('mac_new', '')}</td><td>{inp('ip_new', '')}</td><td></td></tr></table><button>Save</button>")
    if sec == "flight":
        f = c["flight"]; rd = c["radar"]; m = c["marine"]; sp = c["speedtest"]; pw = c["power"]
        return (f"<p>{chk('on', f['on'])} Flight brief card (LAANC ceiling at your lat/lon, TFRs nearby, sunrise/sunset/golden hour, Kp, wind at 400 ft, go/no-go)</p>"
                f"<div class=row>{field('No-go above wind (mph, at 400 ft)', inp('wind', f['max_wind_mph'], 'number'))}{field('No-go at Kp ≥', inp('kp', f['max_kp'], 'number'))}{field('TFR search radius (nm)', inp('rad', f['tfr_radius_nm'], 'number'))}</div>"
                f"<h2>Radar · tropics · marine</h2><div class=row>{field('NWS radar site (radar.weather.gov)', inp('station', rd['station']))}{field('NWS marine zone (e.g. GMZ650)', inp('zone', m['zone']))}{field('Surf-zone forecast office (e.g. MOB)', inp('surf', m['surf_office']))}</div>"
                f"<h2>Pi extras</h2><p>{chk('power', pw['on'])} PiSugar battery / mains watch &nbsp;&nbsp; {chk('speed', sp['on'])} hourly speed test from the Pi</p><div class=row>{field('Speed test every (minutes)', inp('speedmin', sp['interval_min'], 'number'))}</div><button>Save</button>")
    if sec == "security":
        s = c["security"]
        return (f"<p class=dim><b>Honeypot:</b> the Pi listens on ports nothing legitimate should ever touch; any connection is logged on the Security card and pushed to your phone. <b>Nightly audit:</b> nmap of the LAN; new open ports / new hosts show up as a diff.</p>"
                f"<div class=row>{field('Honeypot ports (comma list)', inp('ports', ','.join(str(p) for p in s['canary_ports'])))}{field('Audit hour (0-23)', inp('hour', s['audit_hour'], 'number'))}{field('Audit range (blank = the Pi subnet)', inp('range', s['audit_range']))}</div>"
                f"<p class=dim>Changing ports restarts housedash-canary.</p><button>Save</button>")
    if sec == "alerts":
        a = c["alerts"]
        return (f"<div class=row>{field('Quiet hours start (0-23)', inp('qs', a['quiet_start'], 'number'))}{field('Quiet hours end', inp('qe', a['quiet_end'], 'number'))}</div>"
                f"<div class=row>{field('Device counts as \"back\" after (hours away)', inp('gap', a['return_gap_h'], 'number', '0.5'))}{field('Announce the same device at most every (hours)', inp('cool', a['return_cooldown_h'], 'number', '0.5'))}</div>"
                f"<div class=row>{field('New-device banner (seconds)', inp('bn', a['banner_new_s'], 'number'))}{field('Returned banner (seconds)', inp('br', a['banner_return_s'], 'number'))}</div>"
                f"<p>{chk('sn', a['sound_new'])} Leeroy on new device (always, even in quiet hours) &nbsp;&nbsp; {chk('sr', a['sound_return'])} chime on returning device (silent in quiet hours)</p><button>Save</button>")
    if sec == "wire":
        t = c["ticker"]
        tags = "".join(f"<tr><td class=k>{inp(f'tag{i}', k)}</td><td><input type=color name='col{i}' value='{E(v)}'></td></tr>" for i, (k, v) in enumerate(t["tags"].items()))
        feeds = "".join(f"<tr><td class=c>{chk(f'fon{i}', f.get('on', True))}</td><td class=k>{inp(f'ftag{i}', f.get('tag', 'NEWS'))}</td><td class=k>{inp(f'fname{i}', f['name'])}</td><td>{inp(f'furl{i}', f['url'], 'url')}</td><td class=n>{inp(f'fn{i}', f.get('n', 5), 'number')}</td><td class=c><input type=checkbox name='fdel{i}' title='remove'></td></tr>" for i, f in enumerate(t["feeds"]))
        return (f"<p>{chk('on', t['on'])} show the wire &nbsp;&nbsp; {chk('ufc', t['ufc'])} UFC results + upcoming card (ESPN)</p>"
                f"<div class=row>{field('Label', inp('label', t['label']))}{field('Speed (px/s)', inp('speed', t['speed'], 'number'))}{field('Max items', inp('max', t['max_items'], 'number'))}{field('Category order (comma list)', inp('order', ','.join(t['order'])))}</div>"
                f"<h2>Feeds</h2><p class=dim>Any RSS/Atom URL. Tag groups items and picks the color; 'n' = how many of that feed's newest items to take. Tick the last column to remove.</p>"
                f"<table><tr><th>on</th><th>tag</th><th>name</th><th>url</th><th>n</th><th>✕</th></tr>{feeds}<tr><td class=c>{chk('fon_new', True)}</td><td class=k>{inp('ftag_new', '')}</td><td class=k>{inp('fname_new', '')}</td><td>{inp('furl_new', '', 'url')}</td><td class=n>{inp('fn_new', 5, 'number')}</td><td></td></tr></table>"
                f"<h2>Tag colors</h2><table><tr><th>tag</th><th>color</th></tr>{tags}<tr><td class=k>{inp('tag_new', '')}</td><td><input type=color name=col_new value='#4cc2ff'></td></tr></table><button>Save</button>")
    if sec == "weather":
        w, td = c["weather"], c["tides"]
        return (f"<div class=row>{field('Place label', inp('label', w['label']))}{field('Latitude', inp('lat', w['lat'], 'number', 'any'))}{field('Longitude', inp('lon', w['lon'], 'number', 'any'))}</div>"
                f"<div class=row>{field('Drone \"good\" up to (mph)', inp('fg', w['fly_good_mph'], 'number'))}{field('Drone \"ok\" up to (mph)', inp('fo', w['fly_ok_mph'], 'number'))}</div>"
                f"<h2>Tides (NOAA CO-OPS station ids)</h2><div class=row>{field('Tide station', inp('ts', td['station']))}{field('Station label', inp('tl', td['label']))}{field('Water-temp station', inp('ws', td['water_temp_station']))}</div>"
                f"<p class=dim>Find stations at tidesandcurrents.noaa.gov/map — Gulf State Park Pier is 8731269, Dauphin Island 8735180, Mobile State Docks 8737048, Pensacola 8729840.</p><button>Save</button>")
    if sec == "printers": return dict_table(c, "printers", "IP", "name", "Anycubic printers in LAN mode. Changing this restarts the printer watcher.")
    if sec == "lights": return dict_table(c, "lights", "IP", "name", "WiZ bulbs (UDP 38899).")
    if sec == "hosts": return dict_table(c, "hosts", "IP", "name", "Devices shown in the Devices card (presence from Pi-hole).")
    if sec == "vpn": return dict_table(c, "wg_names", "tunnel IP", "name", "Names for WireGuard peers by their 10.66.66.x address.")
    if sec == "sites":
        rows = "".join(f"<tr><td class=k>{inp(f'key{i}', s['key'])}</td><td class=k>{inp(f'name{i}', s['name'])}</td><td>{inp(f'url{i}', s['url'], 'url')}</td><td class=c>{chk(f'wp{i}', s.get('wp'))}</td><td class=c><input type=checkbox name='del{i}'></td></tr>" for i, s in enumerate(c["sites"]))
        return (f"<p class=dim>Up/down, response time, TLS days left and a defacement fingerprint for each. Tick 'wp' for a WordPress site whose leads/comments you want (then add its app password under WordPress). Last column removes.</p>"
                f"<table><tr><th>key</th><th>name</th><th>url</th><th>wp</th><th>✕</th></tr>{rows}<tr><td class=k>{inp('key_new', '')}</td><td class=k>{inp('name_new', '')}</td><td>{inp('url_new', '', 'url')}</td><td class=c>{chk('wp_new', False)}</td><td></td></tr></table><button>Save</button>")
    if sec == "calendars":
        items = "".join(f"<li>{E(n)} <form method=post action=/cal/remove style=display:inline><input type=hidden name=name value='{E(n)}'><button class=x>remove</button></form></li>" for n in cals()) or "<li class=dim>none yet</li>"
        return (f"<p class=dim>Shows today + tomorrow on the TV. Any Google / iCloud / Outlook calendar.</p><ol class=help><li>On a PC: <b>calendar.google.com</b> → gear → Settings → the calendar on the left → <b>Integrate calendar</b> → copy <b>Secret address in iCal format</b>.</li><li>Phone: open calendar.google.com in the browser and tap “Desktop site”.</li></ol>"
                f"<div class=row>{field('Name', inp('name', ''))}{field('iCal URL', inp('url', '', 'url'))}{field('Color', '<input type=color name=color value=#4cc2ff>')}</div><button>Add calendar</button><p class=dim>Configured:</p><ul>{items}</ul>")
    if sec == "wordpress":
        wps = [s for s in c["sites"] if s.get("wp")]
        opts = "".join(f"<option value='{E(s['key'])}'>{E(s['name'])}</option>" for s in wps)
        state = "<span class=ok>✓ a password is saved</span>" if os.path.exists(ENV) else "<span class=bad>none saved yet</span>"
        return (f"<p class=dim>Lets the Websites card show new leads and comments awaiting moderation. {state}</p><ol class=help><li>Log in at the site's <b>/wp-admin</b> → Users → Profile.</li><li>Scroll to <b>Application Passwords</b> → name it <b>housedash</b> → Add New → copy the 24-character password.</li></ol>"
                f"<div class=row>{field('Site', f'<select name=key>{opts}</select>')}{field('WordPress username', inp('user', ''))}{field('Application password', '<input type=password name=pw placeholder=\"xxxx xxxx xxxx xxxx xxxx xxxx\" autocomplete=off>')}</div><button>Save</button>")
    if sec == "json":
        return f"<p class=dim>The whole effective config. Edit and save; invalid JSON is rejected. (Secrets live elsewhere and are not in here.)</p><textarea name=json>{E(json.dumps(c, indent=1))}</textarea><br><button>Save JSON</button>"
    return ""

def page(msg=""):
    c = hdconfig.load()
    nav = "".join(f"<a href='#{s}'>{s}</a>" for s in SECTIONS)
    body = "".join(f"<h2 id={s}>{s}</h2><div class=card><form method=post action=/{('cal' if s == 'calendars' else 'wp' if s == 'wordpress' else 'cfg/' + s)}>{section_html(c, s)}</form></div>" for s in SECTIONS)
    return (f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>House dashboard setup</title><style>{CSS}</style></head><body>"
            f"<h1>House dashboard setup</h1><p class=dim>Changes apply to the TVs within a few seconds. Dashboard: <a style=color:#4cc2ff href=http://{c['network']['pi_ip']}:8088/>{c['network']['pi_ip']}:8088</a></p><nav>{nav}</nav>{msg}{body}</body></html>")

# ---------- form parsing ----------
def g(d, k, default=""): return d.get(k, [default])[0].strip() if d.get(k) else default
def num(d, k, cur, cast=float):
    v = g(d, k)
    try: return cast(v) if v != "" else cur
    except ValueError: return cur
def on(d, k): return k in d

def parse_dict(d, c, key):
    out = {}
    for i in range(len(c[key])):
        k, v = g(d, f"k{i}"), g(d, f"v{i}")
        if k: out[k] = v or k
    nk = g(d, "newk")
    if nk: out[nk] = g(d, "newv") or nk
    c[key] = out

def apply_cfg(sec, d, c):
    if sec == "general":
        c["title"] = g(d, "title", c["title"]); c["grid"]["columns"] = num(d, "cols", c["grid"]["columns"], int); c["grid"]["rows"] = num(d, "rows", c["grid"]["rows"], int)
        for k in ("page", "collect", "internet", "sites", "outdoors", "ticker"): c["intervals"][k] = num(d, k, c["intervals"][k], int)
        c["network"]["ddns_host"] = g(d, "ddns", c["network"]["ddns_host"])
    elif sec == "cards":
        cards = []
        for i, x in enumerate(c["cards"]):
            cards.append({**x, "on": on(d, f"on{i}"), "label": g(d, f"label{i}", x.get("label", "")), "tall": on(d, f"tall{i}"), "_pos": num(d, f"pos{i}", i + 1, float)})
        cards.sort(key=lambda x: x["_pos"]); [x.pop("_pos") for x in cards]; c["cards"] = cards
    elif sec == "notify":
        n = c["notify"]; n["on"] = on(d, "on"); n["quiet_respect"] = on(d, "quiet")
        n["server"] = g(d, "server", n["server"]).rstrip("/") or "https://ntfy.sh"; n["topic"] = re.sub(r"[^A-Za-z0-9_-]", "", g(d, "topic", n["topic"]))[:64] or n["topic"]
        n["events"] = {k[3:]: True for k in d if k.startswith("ev_")} | {k: False for k in n["events"] if ("ev_" + k) not in d}
    elif sec == "people":
        ppl = []
        for i in range(len(c["people"])):
            if g(d, f"k{i}"): ppl.append({"name": g(d, f"k{i}"), "ip": g(d, f"v{i}")})
        if g(d, "newk"): ppl.append({"name": g(d, "newk"), "ip": g(d, "newv")})
        c["people"] = ppl; c["rules"]["away_minutes"] = num(d, "away", c["rules"]["away_minutes"], int); c["rules"]["lights_off_when_away"] = on(d, "lightsoff")
    elif sec == "wol":
        w = []
        for i, x in enumerate(c["wol"]):
            if on(d, f"del{i}") or not g(d, f"mac{i}"): continue
            w.append({"name": g(d, f"name{i}") or x["name"], "mac": g(d, f"mac{i}").lower(), "ip": g(d, f"ip{i}")})
        if g(d, "mac_new"): w.append({"name": g(d, "name_new") or "PC", "mac": g(d, "mac_new").lower(), "ip": g(d, "ip_new")})
        c["wol"] = w
    elif sec == "flight":
        f = c["flight"]; f["on"] = on(d, "on"); f["max_wind_mph"] = num(d, "wind", f["max_wind_mph"], int); f["max_kp"] = num(d, "kp", f["max_kp"], int); f["tfr_radius_nm"] = num(d, "rad", f["tfr_radius_nm"], int)
        c["radar"]["station"] = g(d, "station", c["radar"]["station"]).upper(); c["marine"]["zone"] = g(d, "zone", c["marine"]["zone"]).upper(); c["marine"]["surf_office"] = g(d, "surf", c["marine"]["surf_office"]).upper()
        c["power"]["on"] = on(d, "power"); c["speedtest"]["on"] = on(d, "speed"); c["speedtest"]["interval_min"] = max(10, num(d, "speedmin", c["speedtest"]["interval_min"], int))
    elif sec == "security":
        s = c["security"]
        s["canary_ports"] = sorted({int(p) for p in re.findall(r"\d+", g(d, "ports"))} - {22, 53, 80, 443, 8088, 8089}) or s["canary_ports"]
        s["audit_hour"] = num(d, "hour", s["audit_hour"], int) % 24; s["audit_range"] = g(d, "range")
    elif sec == "alerts":
        a = c["alerts"]
        a["quiet_start"] = num(d, "qs", a["quiet_start"], int) % 24; a["quiet_end"] = num(d, "qe", a["quiet_end"], int) % 24
        a["return_gap_h"] = num(d, "gap", a["return_gap_h"]); a["return_cooldown_h"] = num(d, "cool", a["return_cooldown_h"])
        a["banner_new_s"] = num(d, "bn", a["banner_new_s"], int); a["banner_return_s"] = num(d, "br", a["banner_return_s"], int)
        a["sound_new"] = on(d, "sn"); a["sound_return"] = on(d, "sr")
    elif sec == "wire":
        t = c["ticker"]; t["on"] = on(d, "on"); t["ufc"] = on(d, "ufc"); t["label"] = g(d, "label", t["label"]) or "WIRE"
        t["speed"] = num(d, "speed", t["speed"], int); t["max_items"] = num(d, "max", t["max_items"], int)
        t["order"] = [x.strip().upper() for x in g(d, "order", "").split(",") if x.strip()] or t["order"]
        feeds = []
        for i, f in enumerate(t["feeds"]):
            if on(d, f"fdel{i}") or not g(d, f"furl{i}"): continue
            feeds.append({"on": on(d, f"fon{i}"), "tag": (g(d, f"ftag{i}") or "NEWS").upper(), "name": g(d, f"fname{i}") or f["name"], "url": g(d, f"furl{i}"), "n": num(d, f"fn{i}", f.get("n", 5), int)})
        if g(d, "furl_new"): feeds.append({"on": on(d, "fon_new"), "tag": (g(d, "ftag_new") or "NEWS").upper(), "name": g(d, "fname_new") or g(d, "furl_new").split("/")[2], "url": g(d, "furl_new"), "n": num(d, "fn_new", 5, int)})
        t["feeds"] = feeds
        tags = {}
        for i in range(len(t["tags"])):
            k = g(d, f"tag{i}").upper()
            if k: tags[k] = g(d, f"col{i}") or "#e6edf3"
        if g(d, "tag_new"): tags[g(d, "tag_new").upper()] = g(d, "col_new") or "#e6edf3"
        t["tags"] = tags
    elif sec == "weather":
        w, td = c["weather"], c["tides"]
        w["label"] = g(d, "label", w["label"]); w["lat"] = num(d, "lat", w["lat"]); w["lon"] = num(d, "lon", w["lon"])
        w["fly_good_mph"] = num(d, "fg", w["fly_good_mph"], int); w["fly_ok_mph"] = num(d, "fo", w["fly_ok_mph"], int)
        td["station"] = g(d, "ts", td["station"]); td["label"] = g(d, "tl", td["label"]); td["water_temp_station"] = g(d, "ws", td["water_temp_station"])
    elif sec in ("printers", "lights", "hosts"): parse_dict(d, c, sec)
    elif sec == "vpn": parse_dict(d, c, "wg_names")
    elif sec == "sites":
        sites = []
        for i, s in enumerate(c["sites"]):
            if on(d, f"del{i}") or not g(d, f"url{i}"): continue
            sites.append({"key": g(d, f"key{i}") or s["key"], "name": g(d, f"name{i}") or s["name"], "url": g(d, f"url{i}").rstrip("/") + "/", "wp": on(d, f"wp{i}")})
        if g(d, "url_new"):
            u = g(d, "url_new").rstrip("/") + "/"; k = g(d, "key_new") or u.split("/")[2].split(".")[0]
            sites.append({"key": k, "name": g(d, "name_new") or u.split("/")[2], "url": u, "wp": on(d, "wp_new")})
        c["sites"] = sites
    elif sec == "json":
        new = json.loads(g(d, "json"))
        if not isinstance(new, dict) or "cards" not in new: raise ValueError("that doesn't look like a dashboard config")
        c.clear(); c.update(new)

def wake(mac):
    """Send a Wake-on-LAN magic packet to the broadcast address."""
    m = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(m) != 6: raise ValueError("bad MAC")
    pkt = b"\xff" * 6 + m * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    brds = {"255.255.255.255"}
    try: brds |= set(re.findall(r"brd (\d+\.\d+\.\d+\.\d+)", subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True).stdout))
    except Exception: pass
    for addr in brds:
        for port in (9, 7):
            try: s.sendto(pkt, (addr, port))
            except Exception: pass
    s.close()

def api(path, q, c):
    """Actions for the dashboard's phone/PC view. Returns a dict."""
    if path == "/api/wake":
        name = q.get("name"); t = next((w for w in c["wol"] if w["name"] == name), None)
        if not t: raise ValueError("unknown wake target")
        wake(t["mac"]); return {"ok": True, "woke": name}
    if path == "/api/light":
        ip = q.get("ip");
        if ip not in c["lights"]: raise ValueError("unknown light")
        act = q.get("action", "toggle"); dim = q.get("dim")
        if act == "toggle": r = wizctl.toggle(ip)
        else: r = wizctl.set_state(ip, on=(act == "on"), dim=int(dim) if dim else None)
        return {"ok": "error" not in r, "ip": ip, "result": r}
    if path == "/api/lights":
        act = q.get("action", "off"); dim = q.get("dim")
        wizctl.all_lights(c["lights"], act == "on", int(dim) if dim else None); return {"ok": True, "all": act}
    if path == "/api/notify/test":
        ok = notify.send("test", "House dashboard", "test notification - it works", tags=["tada"]); return {"ok": ok, "sent": ok}
    raise ValueError("unknown api")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, code=200, ctype="text/html; charset=utf-8"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self): self.send("", 204)
    def do_GET(self):
        if self.path.split("?")[0] != "/": return self.send("", 404)
        c = hdconfig.load()
        if not c["notify"].get("topic"):   # first visit: mint a private topic
            o = copy.deepcopy(c); o["notify"]["topic"] = "housedash-" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]; hdconfig.save(o)
        self.send(page())
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n).decode()
        d = urllib.parse.parse_qs(raw, keep_blank_values=True)
        p = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}
        if p.path.startswith("/api/"):
            try: res = api(p.path, q or {k: v[0] for k, v in d.items()}, hdconfig.load()); code = 200
            except Exception as e: res = {"ok": False, "err": str(e)[:120]}; code = 400
            if "text/html" in (self.headers.get("Accept") or "") and p.path == "/api/notify/test":   # the settings page's button
                return self.send(page(f"<div class='msg {'ok' if res.get('ok') else 'bad'}'>{'✓ test notification sent' if res.get('ok') else '✗ not sent: ' + E(str(res.get('err') or 'notifications off or no topic'))}</div>"))
            return self.send(json.dumps(res), code, "application/json")
        try:
            if self.path.startswith("/cfg/"):
                sec = self.path[5:]; c = copy.deepcopy(hdconfig.load()); apply_cfg(sec, d, c); hdconfig.save(c)
                if sec in ("printers", "json"): restart("housedash-printers")
                if sec in ("security", "json"): restart("housedash-canary")
                msg = f"<div class='msg ok'>✓ {E(sec)} saved — the TVs pick it up within ~10 s.</div>"
            elif self.path == "/cal":
                name, url, color = g(d, "name").replace("|", "/") or "Calendar", g(d, "url"), g(d, "color") or "#4cc2ff"
                test_ics(url); write_cals(add=f"{name} | {color} | {url}")
                msg = f"<div class='msg ok'>✓ added {E(name)} — on the TV within a minute.</div>"
            elif self.path == "/cal/remove":
                write_cals(keep=[x for x in cals() if x != g(d, "name")]); msg = f"<div class='msg ok'>removed {E(g(d, 'name'))}</div>"
            elif self.path == "/wp":
                c = hdconfig.load(); site = next((s for s in c["sites"] if s["key"] == g(d, "key")), None)
                if not site: raise ValueError("pick a site")
                user, pw = g(d, "user"), g(d, "pw").replace(" ", "")
                who = test_wp(site["url"], user, pw)
                envk = site.get("env") or site["key"].upper()
                lines = [l for l in (open(ENV).read().splitlines() if os.path.exists(ENV) else []) if not l.startswith(envk + "_WP_")]
                lines += [f"{envk}_WP_USER={user}", f"{envk}_WP_PASS={pw}"]
                with open(os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: f.write("\n".join(lines) + "\n")
                restart("housedash-sites")
                msg = f"<div class='msg ok'>✓ {E(site['name'])} connected as {E(str(who))} — leads and comments show within a minute.</div>"
            else:
                return self.send("", 404)
        except Exception as e:
            msg = f"<div class='msg bad'>✗ {E(str(e)[:200])}</div>"
        self.send(page(msg))

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8089), H).serve_forever()
