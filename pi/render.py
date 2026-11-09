#!/usr/bin/env python3
"""Draw the dashboard to a PNG for screens that can't run a browser (the Roku screensaver channel in roku/).

Reads the same state.json and config the web page uses - same cards, same order, same grid - and writes
/var/lib/housedash/roku.png every few seconds (config roku.interval). Pure Pillow, ~1 s on a Pi Zero 2 W.
    render.py            run forever as a service
    render.py --once     render one frame and exit (handy for a look: scp the PNG somewhere)"""
import io, json, os, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
import hdconfig

STATE, OUT = "/var/lib/housedash/state.json", "/var/lib/housedash/roku.png"
W, H = 1920, 1080
BG, CARD, LINE, FG, DIM, ACC, OK, WARN, BAD = "#0b0f14", "#121923", "#1f2a38", "#e6edf3", "#8b98a8", "#4cc2ff", "#3ddc84", "#ffd866", "#ff5c5c"
FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
_fonts = {}
def F(size, bold=False):
    k = (size, bold)
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(FONT_DIR + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
    return _fonts[k]

class Pane:
    """A card's drawable area with a cursor; text helpers clip and wrap so nothing spills out."""
    def __init__(self, d, x, y, w, h, hy=None):
        self.d, self.x, self.y, self.w, self.h, self.cy = d, x, y, w, h, y
        self.hy = hy if hy is not None else y - 32   # the card's header line, for a badge on the right (like the page's h2)
    def badge(self, s, color, bg, size=15):
        tw = self.d.textlength(s, font=F(size, True)) + 20
        self.tag(s, color, bg, size, x=self.x + self.w - tw, y=self.hy - 4)
    @property
    def left(self): return self.h - (self.cy - self.y)
    def fit(self, s, font, maxw):
        s = str(s)
        if self.d.textlength(s, font=font) <= maxw: return s
        while s and self.d.textlength(s + "…", font=font) > maxw: s = s[:-1]
        return s + "…"
    def text(self, s, size=20, color=FG, bold=False, x=None, dy=6, maxw=None):
        if self.left < size + 4: return False
        f = F(size, bold); x = self.x if x is None else x
        self.d.text((x, self.cy), self.fit(s, f, maxw or (self.x + self.w - x)), font=f, fill=color)
        self.cy += size + dy; return True
    def row(self, left, right, size=20, lc=FG, rc=DIM, dot=None, dy=8):
        """name on the left, value on the right, optional status dot; the name gives way to the value."""
        if self.left < size + 4: return False
        f = F(size); x = self.x
        if dot:
            r = size // 3; self.d.ellipse((x, self.cy + size // 2 - r + 2, x + 2 * r, self.cy + size // 2 + r + 2), fill=dot); x += 2 * r + 10
        lw = self.d.textlength(str(left), font=f) + (x - self.x)
        rw = min(self.d.textlength(str(right), font=f), max(self.w * 0.45, self.w - lw - 16))   # value gets the room the name doesn't need
        self.d.text((self.x + self.w - rw, self.cy), self.fit(right, f, rw), font=f, fill=rc)
        self.d.text((x, self.cy), self.fit(left, f, self.x + self.w - rw - 16 - x), font=f, fill=lc)
        self.cy += size + dy
        self.d.line((self.x, self.cy - dy // 2, self.x + self.w, self.cy - dy // 2), fill=LINE); return True
    def wrap(self, s, size=18, color=FG, maxlines=3, dy=4):
        f = F(size); words, line, n = str(s).split(), "", 0
        for w in words:
            t = (line + " " + w).strip()
            if self.d.textlength(t, font=f) > self.w and line:
                if n == maxlines - 1: self.text(line + "…", size, color, dy=dy); return
                self.text(line, size, color, dy=dy); line, n = w, n + 1
            else: line = t
        if line: self.text(line, size, color, dy=dy)
    def tag(self, s, color, bg, size=18, x=None, y=None):
        f = F(size, True); x = self.x if x is None else x; y = self.cy if y is None else y
        tw = self.d.textlength(s, font=f)
        self.d.rounded_rectangle((x, y, x + tw + 20, y + size + 10), 8, fill=bg); self.d.text((x + 10, y + 4), s, font=f, fill=color)
        return x + tw + 28
    def bar(self, pct, color=ACC, h=10):
        self.d.rounded_rectangle((self.x, self.cy, self.x + self.w, self.cy + h), h // 2, fill=LINE)
        if pct > 0: self.d.rounded_rectangle((self.x, self.cy, self.x + max(h, self.w * min(pct, 100) / 100), self.cy + h), h // 2, fill=color)
        self.cy += h + 12
    def space(self, n=8): self.cy += n

def age(s):
    if s is None: return "never"
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h" if s < 86400 else f"{s // 86400}d"
def h12(t):   # "18:00" -> "6p"
    try: h, m = map(int, t.split(":")); return f"{h % 12 or 12}{':%02d' % m if m else ''}{'a' if h < 12 else 'p'}"
    except Exception: return t

_radar = {"t": 0, "img": None, "url": ""}
def radar_frame(url):
    """First frame of the NWS loop GIF, cached 5 min; None if offline."""
    if url and (time.time() - _radar["t"] > 300 or _radar["url"] != url):
        _radar.update(t=time.time(), url=url)
        try:
            with urllib.request.urlopen(url, timeout=6) as r: _radar["img"] = Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception: pass
    return _radar["img"]

# ---- the cards: each takes (pane, state, cfg) ------------------------------------------------------------
def c_wx(p, s, c):
    w = s.get("out", {}).get("weather") or {}
    if not w.get("ok"): return p.text("no weather yet", 20, DIM)
    n = w["now"]; f = F(64, True)
    p.d.text((p.x, p.cy - 6), f"{n['temp']}°", font=f, fill=FG); x = p.x + p.d.textlength(f"{n['temp']}°", font=f) + 18
    fly = w.get("fly", ""); col, bg = {"good": (OK, "#153a26"), "ok": (WARN, "#3a3313")}.get(fly, (BAD, "#3a1515"))
    p.badge(f"drone: {fly}", col, bg)
    p.d.text((x, p.cy + 8), p.fit(n.get("short", ""), F(24), p.w - (x - p.x)), font=F(24), fill=FG)
    p.d.text((x, p.cy + 40), p.fit(f"wind {n.get('wind', 0)} {n.get('dir', '')} · {n.get('humidity', '')}% rh", F(18), p.w - (x - p.x)), font=F(18), fill=DIM)
    p.cy += 76
    hrs = w.get("hours", [])[:6]; cw = p.w / max(1, len(hrs))
    for i, h in enumerate(hrs):
        x = p.x + i * cw; p.d.text((x, p.cy), h12(h["t"]), font=F(15), fill=DIM); p.d.text((x, p.cy + 18), f"{h['temp']}°", font=F(20, True), fill=FG)
        p.d.text((x, p.cy + 42), f"{h.get('rain', 0)}%" if h.get("rain") else f"{h.get('wind', '')}{h.get('dir', '')}", font=F(14), fill=ACC if h.get("rain") else DIM)
    p.cy += 66
    for d in w.get("days", [])[:3]: p.row(f"{d['name']} · {d['short']}", f"{d['temp']}°  {d.get('rain', 0)}%", 18)
    for a in w.get("alerts", [])[:1]: p.text("⚠ " + a if isinstance(a, str) else "⚠ " + str(a.get("event", a)), 18, BAD)

def c_tide(p, s, c):
    t = s.get("out", {}).get("tides") or {}; m = s.get("out", {}).get("marine") or {}
    if not t.get("ok"): return p.text("no tide data", 20, DIM)
    for n in t.get("next", [])[:4]:
        tm = n["t"][11:16]; p.row(f"{'▲ High' if n['type'] == 'high' else '▼ Low'}  {h12(tm)}", f"{n['ft']} ft", 22, ACC if n["type"] == "high" else DIM)
    p.space(6)
    if t.get("water_temp"): p.text(f"water {t['water_temp']}°", 18, DIM)
    rip = (m.get("rip") or "").lower()
    if rip: p.tag(f"rip current: {m['rip']}", *((OK, "#153a26") if rip == "low" else (WARN, "#3a3313") if rip == "moderate" else (BAD, "#3a1515")), 16); p.cy += 36
    if m.get("surf"): p.text(f"surf {m['surf']}", 18, DIM)

def c_fish(p, s, c):
    f = s.get("out", {}).get("fishing") or {}
    half = (p.w - 20) // 2
    for i, (hdr, key) in enumerate((("INSHORE", "inshore"), ("SURF", "surf"))):
        x = p.x + i * (half + 20); y = p.cy
        p.d.text((x, y), hdr, font=F(14, True), fill=DIM); y += 22
        for item in f.get(key, [])[:6]: p.d.text((x, y), p.fit(item, F(18), half), font=F(18), fill=FG); y += 24
    p.cy += 22 + 24 * max(len(f.get("inshore", [])[:6]), len(f.get("surf", [])[:6])) + 8
    tips = f.get("tips", [])
    if tips: p.wrap(tips[int(time.time() // 20) % len(tips)], 17, ACC, maxlines=3)

def c_lan(p, s, c):
    l = s.get("lan") or {}
    if not l.get("ok"): return p.text("Pi-hole unreachable", 20, DIM)
    ppl = l.get("people", [])
    if ppl:
        x = p.x
        for q in ppl:
            if x > p.x + p.w - 120: break
            x = p.tag(("● " if q["home"] else "○ ") + q["name"], OK if q["home"] else DIM, "#153a26" if q["home"] else LINE, 16, x=x)
        p.cy += 40
    for h in l.get("hosts", [])[:40]:
        if not p.row(h["name"], "online" if h.get("online") else age(h.get("seen")), 18, dot=OK if h.get("online") else "#2a3647"): break

def c_pihole(p, s, c):
    q = s.get("pihole") or {}
    if not q.get("ok"): return p.text(q.get("err", "no Pi-hole"), 20, DIM)
    p.d.text((p.x, p.cy - 6), f"{q['pct']}%", font=F(56, True), fill=FG); p.cy += 62
    p.text(f"{q['blocked']:,} of {q['total']:,} blocked · {q['clients']} clients", 17, DIM)
    p.bar(q["pct"])
    for d, n in q.get("top_blocked", [])[:4]: p.row(d, f"{n:,}", 16)

def c_pr(p, s, c):
    pr = s.get("printers") or {}
    for x in pr.get("printers", [])[:3]:
        st = "idle" if x.get("state") in (None, "free") else x["state"]
        if not x.get("lan"): p.row(x["name"], "LAN mode off", 18, dot="#2a3647"); continue
        p.row(f"{x['name']} · {st}", f"{x.get('nozzle', '-')}° / {x.get('bed', '-')}°", 18, dot=OK if st == "printing" else BAD if st == "error" else "#2a3647")
        if st in ("printing", "paused"):
            p.text(f"{x.get('file', '')}  {x.get('progress', 0)}%  {age(x['remaining']) + ' left' if x.get('remaining') else ''}", 16, DIM); p.bar(x.get("progress", 0))
    if not pr.get("printers"): p.text("no printers configured", 18, DIM)

def c_wg(p, s, c):
    w = s.get("wg") or {}
    if not w.get("ok"): return p.text(w.get("err", "no WireGuard"), 18, DIM)
    for x in w.get("peers", [])[:8]: p.row(x["name"], "connected" if x.get("up") else ("never" if x.get("handshake_age") is None else age(x["handshake_age"]) + " ago"), 18, dot=OK if x.get("up") else "#2a3647")

def c_wiz(p, s, c):
    z = s.get("wiz") or {}; bl = [l for l in (s.get("ble") or {}).get("lights", []) if l.get("named")]
    bulbs = z.get("bulbs", []) if z.get("ok") else []
    cols = 5; gap = 8; bw = (p.w - gap * (cols - 1)) / cols; bh = 36
    for i, b in enumerate(bulbs[:10]):
        x = p.x + (i % cols) * (bw + gap); y = p.cy + (i // cols) * (bh + gap)
        on = b.get("on"); p.d.rounded_rectangle((x, y, x + bw, y + bh), 8, fill="#3a3313" if on else LINE, outline="#7a6a1d" if on else LINE)
        lbl = f"{b.get('dim', 0)}%" if on else ("?" if on is None else "off"); f = F(16)
        p.d.text((x + (bw - p.d.textlength(lbl, font=f)) / 2, y + 9), lbl, font=f, fill=WARN if on else DIM)
    p.cy += ((min(len(bulbs), 10) + cols - 1) // cols) * (bh + gap) + 6
    if bulbs: p.text(f"{z.get('on', 0)} of {len(bulbs)} WiZ on", 15, DIM, dy=10)
    for l in bl[:6]:
        p.row(l["name"], f"{l['rssi']} dBm" if l.get("seen") and l.get("rssi") is not None else "out of range", 17, dot=ACC if l.get("seen") else "#2a3647")

def c_net(p, s, c):
    o = s.get("out", {}); i = o.get("internet") or {}; y = s.get("sys") or {}; sp = s.get("speed") or {}; pw = s.get("power") or {}
    cf = i.get("cloudflare") or {}
    p.row("Internet", f"{cf['ms']} ms" if cf.get("ms") is not None else "down", 19, dot=BAD if cf.get("loss") or cf.get("ms") is None else OK)
    if i.get("public_ip"): p.row("Public IP · DDNS " + ("ok" if i.get("ddns_ok") else "MISMATCH"), i["public_ip"], 17, dot=OK if i.get("ddns_ok") else WARN)
    if y.get("ok"): p.row("Pi", f"{y['temp']}° · load {y['load']} · mem {y['mem_pct']}% · up {age(y['uptime'])}", 17)
    if sp.get("last"): p.row("Speed", f"↓{sp['last']['down']} ↑{sp['last']['up']} Mb/s · {sp['last']['ping']} ms", 17)
    if pw.get("ok"): p.row("Power", ("mains · " if pw.get("plugged") else "ON BATTERY · ") + f"{pw.get('pct', '?')}%", 17, dot=OK if pw.get("plugged") else BAD)
    d = s.get("dns") or {}
    if d: p.row("DNS", f"{d.get('upstream', '')}{'' if d.get('healthy', True) else ' · unhealthy'}", 17, dot=OK if d.get("healthy", True) else WARN)

def c_sites(p, s, c):
    st = s.get("sites") or {}
    for x in st.get("sites", [])[:8]:
        v = f"{x['ms']} ms" if x.get("up") else f"DOWN {x.get('status', '')}"
        if x.get("cert_days") is not None and x["cert_days"] <= 14: v += f" · cert {x['cert_days']}d"
        if x.get("domain_days") is not None and x["domain_days"] <= 60: v += f" · domain {x['domain_days']}d"
        if x.get("leads_unread"): v += f" · {x['leads_unread']} leads"
        p.row(x["name"] + (" · changed" if x.get("changed") else ""), v, 18, lc=WARN if x.get("changed") else FG, dot=OK if x.get("up") else BAD)
    if not st.get("sites"): p.text("no websites configured", 18, DIM)

def c_cal(p, s, c, tz=None):
    ca = s.get("cal") or {}
    today = datetime.now(tz).strftime("%Y-%m-%d"); ev = ca.get("events", [])
    td = [e for e in ev if e.get("start", "")[:10] == today]; tm = [e for e in ev if e.get("start", "")[:10] > today]
    if not td: p.text("free day", 20, DIM)
    for e in td[:6]: p.row(e.get("title", ""), "all day" if e.get("allday") else h12(e["start"][11:16]), 18, dot=e.get("color") or ACC)
    if tm:
        p.space(4); p.text("TOMORROW", 13, DIM, True)
        for e in tm[:4]: p.row(e.get("title", ""), "all day" if e.get("allday") else h12(e["start"][11:16]), 17, dot=e.get("color") or ACC)
    if ca.get("calendars", 0) < 2 and not ev: p.text("add calendars at the settings page", 16, DIM)

def c_flight(p, s, c):
    f = s.get("out", {}).get("flight") or {}
    if not f.get("ok"): return p.text("no flight brief", 18, DIM)
    v = f.get("verdict", ""); col, bg = {"GO": (OK, "#153a26"), "CAUTION": (WARN, "#3a3313")}.get(v, (BAD, "#3a1515"))
    p.badge(v, col, bg)
    sun, g, al = f.get("sun", {}), f.get("golden", {}), f.get("aloft", {})
    p.row("Sun", f"{sun.get('sunrise', '')} – {sun.get('sunset', '')}", 17)
    p.row("Golden hour", f"{g.get('am', '')} · {g.get('pm', '')}", 17)
    p.row("Wind @ 400 ft", f"{al.get('w120', '?')} mph · gust {al.get('gust', '?')} · max 6 h {al.get('w120_max6h', '?')}", 17)
    p.row("Airspace", f"class {f.get('class', '?')} · {'LAANC grid' if (f.get('laanc') or {}).get('grid') else 'no LAANC needed'} · Kp {f.get('kp', '?')}", 17)
    if f.get("tfrs"): p.row("TFRs", f"{len(f['tfrs'])} within range", 17, lc=BAD)
    for r in f.get("nogo", [])[:2]: p.text("• " + r, 16, WARN)

def c_radar(p, s, c):
    r = s.get("out", {}).get("radar") or {}; tr = s.get("out", {}).get("tropics") or {}
    img = radar_frame(r.get("img"))
    storms = tr.get("atlantic") or []; reserve = 30 * min(len(storms), 3) + (10 if storms else 0)
    ih = p.h - reserve - 4
    if img is not None and ih > 60:
        im = img.copy(); im.thumbnail((p.w, ih))
        p.d._image.paste(im, (int(p.x + (p.w - im.width) / 2), int(p.cy))); p.cy += ih + 4
    else:
        p.text(f"radar {r.get('station', '')} unavailable", 18, DIM); p.cy += 4
    for st in storms[:3]: p.row(f"{st['class']} {st['name']}", f"{st['kt']} kt · {st['move']}", 17, lc=BAD)
    if not storms and img is not None: pass

def c_security(p, s, c):
    se = s.get("security") or {}; now = time.time()
    hits = [h for h in se.get("canary", []) if now - h["ts"] < 86400]
    p.row("Honeypot (24 h)", f"{len(hits)} touch{'es' if len(hits) != 1 else ''}", 18, dot=BAD if hits else OK)
    for h in hits[-3:][::-1]: p.text(f"{datetime.fromtimestamp(h['ts']).strftime('%H:%M')}  {h['src']} → {h['svc']}", 16, DIM)
    a = se.get("audit") or {}
    if a:
        d = a.get("diff") or {}; changes = sum(len(v) for v in d.values()) if isinstance(d, dict) else 0
        p.row("Port audit", f"{a.get('hosts', '?')} hosts · {'baseline' if a.get('first') else f'{changes} changes' if changes else 'no changes'}", 18, dot=WARN if changes else OK)
        for ip in (d.get("new_hosts") or [])[:2]: p.text(f"new host {ip}", 16, WARN)
        for x in (d.get("new_ports") or [])[:2]: p.text(f"new port {x}", 16, WARN)

CARDS = {"wx": c_wx, "tide": c_tide, "fish": c_fish, "lan": c_lan, "pihole": c_pihole, "pr": c_pr, "wg": c_wg, "wiz": c_wiz,
         "net": c_net, "sites": c_sites, "cal": c_cal, "flight": c_flight, "radar": c_radar, "security": c_security}

def place(cards, cols, rows):
    """Same auto-placement as the CSS grid: row-major, a tall card takes this cell and the one below it."""
    grid = [[None] * cols for _ in range(rows)]; out = []
    for card in cards:
        done = False
        for r in range(rows):
            for col in range(cols):
                if grid[r][col]: continue
                tall = card.get("tall") and r + 1 < rows and not grid[r + 1][col]
                grid[r][col] = card["id"]
                if tall: grid[r + 1][col] = card["id"]
                out.append((card, r, col, 2 if tall else 1)); done = True; break
            if done: break
    return out

def render(s, cfg):
    tz = None
    try: tz = ZoneInfo(s.get("tz") or cfg.get("timezone") or "UTC")
    except Exception: pass
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im); d._image = im
    now = datetime.now(tz)
    # header
    d.text((40, 22), cfg.get("title", "House"), font=F(38, True), fill=FG)
    clock = now.strftime("%-I:%M %p").lower(); date = now.strftime("%A, %B %-d")
    d.text((W - 40 - d.textlength(clock, font=F(44, True)), 14), clock, font=F(44, True), fill=FG)
    d.text((W - 40 - d.textlength(date, font=F(20)), 64), date, font=F(20), fill=DIM)
    # grid
    cols, rows = int(cfg["grid"]["columns"]), int(cfg["grid"]["rows"])
    top, bottom, gap, pad = 100, H - 70, 14, 18
    cw = (W - 80 - gap * (cols - 1)) / cols; ch = (bottom - top - gap * (rows - 1)) / rows
    labels = {"wx": "Weather · " + cfg["weather"]["label"], "tide": "Tide · " + cfg["tides"]["label"]}
    for card, r, col, span in place([x for x in cfg["cards"] if x.get("on", True) and x["id"] in CARDS], cols, rows):
        x0, y0 = 40 + col * (cw + gap), top + r * (ch + gap); x1, y1 = x0 + cw, y0 + span * ch + (span - 1) * gap
        d.rounded_rectangle((x0, y0, x1, y1), 16, fill=CARD, outline=LINE)
        lbl = card.get("label") or card["id"]
        if card["id"] in labels and lbl in ("Weather", "Tide"): lbl = labels[card["id"]]
        d.text((x0 + pad, y0 + 14), lbl.upper(), font=F(15, True), fill=DIM)
        pane = Pane(d, x0 + pad, y0 + 44, cw - 2 * pad, y1 - y0 - 44 - pad, hy=y0 + 14)
        try:
            (lambda p: c_cal(p, s, cfg, tz))(pane) if card["id"] == "cal" else CARDS[card["id"]](pane, s, cfg)
        except Exception as e:
            pane.text(f"{type(e).__name__}: {e}"[:60], 14, BAD)
    # wire
    t = s.get("ticker") or {}; items = t.get("items", []); tags = cfg.get("ticker", {}).get("tags", {})
    d.rounded_rectangle((40, H - 56, W - 40, H - 14), 12, fill=CARD, outline=LINE)
    x = 56; d.text((x, H - 45), cfg.get("ticker", {}).get("label", "WIRE"), font=F(16, True), fill=DIM); x += 80
    if items:
        start = int(time.time() // max(5, int(cfg.get("roku", {}).get("interval", 15)))) % len(items)
        for i in range(len(items)):
            it = items[(start + i) % len(items)]; tag = it.get("tag", "")
            tf, f = F(14, True), F(18); need = d.textlength(tag, font=tf) + 12 + d.textlength(it.get("text", ""), font=f) + 40
            if x + need > W - 60:
                rest = W - 60 - x - d.textlength(tag, font=tf) - 12
                if rest > 160:
                    d.text((x, H - 44), tag, font=tf, fill=tags.get(tag, FG)); x += d.textlength(tag, font=tf) + 12
                    txt = it.get("text", "")
                    while txt and d.textlength(txt + "…", font=f) > rest: txt = txt[:-1]
                    d.text((x, H - 46), txt + "…", font=f, fill=FG)
                break
            d.text((x, H - 44), tag, font=tf, fill=tags.get(tag, FG)); x += d.textlength(tag, font=tf) + 12
            d.text((x, H - 46), it.get("text", ""), font=f, fill=FG); x += d.textlength(it.get("text", ""), font=f) + 40
    stale = int(time.time()) - int(s.get("ts", 0))
    if stale > 120: d.text((40, 70), f"data {age(stale)} old", font=F(16), fill=BAD)
    return im

def once(out=OUT):
    cfg = hdconfig.load()
    with open(STATE) as f: s = json.load(f)
    im = render(s, cfg); tmp = out + ".tmp"; im.save(tmp, "PNG", optimize=False, compress_level=3); os.replace(tmp, out)
    return im

def main():
    if "--once" in sys.argv:
        once(sys.argv[-1] if sys.argv[-1].endswith(".png") else OUT); print("ok"); return
    while True:
        cfg = hdconfig.load(); rk = cfg.get("roku") or {}; iv = max(5, int(rk.get("interval", 15)))
        if not rk.get("on"):
            time.sleep(30); continue   # switched off in config: idle, no CPU spent
        t0 = time.time()
        try: once()
        except Exception as e: print("render:", e, file=sys.stderr)
        time.sleep(max(1, iv - (time.time() - t0)))

if __name__ == "__main__":
    main()
