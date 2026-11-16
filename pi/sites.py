#!/usr/bin/env python3
"""Business websites for the house dashboard. Every 5 min: up/down, response time,
TLS certificate days left, and a fingerprint of the home page so a defacement or
broken deploy shows up. For WordPress sites with an application password in
/etc/housedash/sites.env (<KEY>_WP_USER / <KEY>_WP_PASS, KEY = the site's key upper-cased) it also pulls Fluent Forms
entry counts (leads) and comments awaiting moderation. Writes sites.json."""
import base64, hashlib, json, os, re, socket, ssl, time, urllib.request
from datetime import datetime, timezone

OUT = "/var/lib/housedash/sites.json"
ENV = "/etc/housedash/sites.env"
import hdconfig, notify
UA = {"User-Agent": "housedash-monitor/1 (+home dashboard)"}

def env():
    d = {}
    try:
        for line in open(ENV):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); d[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return d

_fails = {}
_rdap = {}   # domain -> (checked_ts, days)

def domain_days(host):
    """Days until the domain registration expires, via RDAP (rdap.org bootstrap). Checked once a day per domain."""
    parts = host.split("."); dom = ".".join(parts[-2:]) if len(parts) >= 2 else host
    c = _rdap.get(dom)
    if c and time.time() - c[0] < 86400: return c[1]
    req = urllib.request.Request(f"https://rdap.org/domain/{dom}", headers={**UA, "Accept": "application/rdap+json"})
    with urllib.request.urlopen(req, timeout=20) as r: d = json.load(r)
    days = None
    for ev in d.get("events", []):
        if ev.get("eventAction") == "expiration":
            exp = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
            days = int((exp - datetime.now(timezone.utc)).total_seconds() // 86400)
    _rdap[dom] = (time.time(), days)
    return days

def cert_days(host):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
        exp = ss.getpeercert()["notAfter"]
    dt = datetime.strptime(exp, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return int((dt - datetime.now(timezone.utc)).total_seconds() // 86400)

def visible(html):
    """What a visitor actually sees: the page text plus where its links and images point (query strings dropped).
    Hashing raw HTML flagged a 'change' every 12 h on WordPress (theme/plugin markup churn - WP nonces tick every
    12 h) with nothing visibly different, so markup, attributes, styles and scripts are ignored. A defacement or
    a broken deploy still changes the text, the links or the images."""
    h = re.sub(rb"<(script|style|noscript|template|svg)\b.*?</\1>", b" ", html, flags=re.S | re.I)
    h = re.sub(rb"<!--.*?-->", b" ", h, flags=re.S)
    refs = [re.sub(rb"[?#].*", b"", u) for u in re.findall(rb'<(?:a|img|iframe)\b[^>]*?\b(?:href|src)=["\']([^"\']+)', h, flags=re.I)]
    refs = sorted(set(r for r in refs if r and not r.startswith((b"#", b"javascript:", b"data:")) and b"email-protection" not in r))
    text = re.sub(rb"<[^>]+>", b" ", h)
    text = re.sub(rb"&#?\w+;", b" ", text)                     # entities (incl. Cloudflare-obfuscated emails' leftovers)
    text = re.sub(rb"\b\d{10,}\b", b"", text)                  # stray timestamps
    text = re.sub(rb"\s+", b" ", text).strip()
    return text.decode("utf-8", "ignore") + "\n\n# links/images\n" + "\n".join(r.decode("utf-8", "ignore") for r in refs)

def fingerprint(html):
    return hashlib.sha256(visible(html).encode()).hexdigest()[:16]

FP_DIR = "/var/lib/housedash/site-fp"   # last visible snapshot per site (survives restarts); <key>.prev.txt kept on a change for diffing
os.makedirs(FP_DIR, exist_ok=True)

def _fp_load(k):
    try: return open(f"{FP_DIR}/{k}.fp").read().strip()
    except Exception: return None

def _fp_save(k, fp, snap, changed):
    cur = f"{FP_DIR}/{k}.txt"
    if changed and os.path.exists(cur): os.replace(cur, f"{FP_DIR}/{k}.prev.txt")
    with open(cur, "w") as f: f.write(snap)
    with open(f"{FP_DIR}/{k}.fp", "w") as f: f.write(fp)

_last_fp = {}

def check(site, creds):
    host = site["url"].split("/")[2]
    r = {"key": site["key"], "name": site["name"], "url": site["url"], "up": False}
    t0 = time.time()
    try:
        req = urllib.request.Request(site["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(400_000)
            r.update(up=200 <= resp.status < 400, status=resp.status, ms=int((time.time() - t0) * 1000))
            title = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            r["title"] = title.group(1).decode("utf-8", "ignore").strip()[:60] if title else ""
            snap = visible(body); fp = hashlib.sha256(snap.encode()).hexdigest()[:16]
            r["fingerprint"] = fp
            k0 = site["key"]
            if k0 not in _last_fp: _last_fp[k0] = _fp_load(k0)
            prev = _last_fp[k0]
            r["changed"] = bool(prev and prev != fp)
            if prev != fp: _fp_save(k0, fp, snap, r["changed"])
            _last_fp[k0] = fp
    except Exception as e:
        r.update(err=str(e)[:70], ms=int((time.time() - t0) * 1000))
    # notifications: down after 2 consecutive failures (no flapping), recovery, content change, cert running out
    k = site["key"]; fails = _fails.get(k, 0)
    if not r["up"]:
        _fails[k] = fails + 1
        if _fails[k] == 2: notify.send("site_down", f"{site['name']} is DOWN", f"{site['url']} · {r.get('err') or r.get('status')}", priority=5, tags=["red_circle"], dedupe="down:" + k, dedupe_s=3600)
    else:
        if fails >= 2: notify.send("site_down", f"{site['name']} is back up", f"{r['ms']} ms", tags=["green_circle"], dedupe="up:" + k, dedupe_s=600)
        _fails[k] = 0
    if r.get("changed"): notify.send("site_changed", f"{site['name']}: home page changed", f"the visible text, links or images changed - diff on the Pi: diff {FP_DIR}/{k}.prev.txt {FP_DIR}/{k}.txt", priority=4, tags=["eyes"], dedupe="chg:" + k, dedupe_s=21600)
    try:
        r["cert_days"] = cert_days(host)
        if r["cert_days"] <= 14: notify.send("cert_expiring", f"{site['name']}: TLS cert expires in {r['cert_days']} d", host, priority=4, tags=["lock"], dedupe="cert:" + k, dedupe_s=86400)
    except Exception as e:
        r["cert_err"] = str(e)[:50]
    try:
        r["domain_days"] = domain_days(host)
        if r["domain_days"] is not None and r["domain_days"] <= 30: notify.send("domain_expiring", f"{site['name']}: domain expires in {r['domain_days']} d", host, priority=4, tags=["globe_with_meridians"], dedupe="dom:" + k, dedupe_s=86400)
    except Exception as e:
        r["domain_err"] = str(e)[:50]
    if site.get("wp"):
        envk = site.get("env") or site["key"].upper()
        u, p = creds.get(envk + "_WP_USER"), creds.get(envk + "_WP_PASS")
        if u and p:
            auth = {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode(), **UA}
            try:
                req = urllib.request.Request(site["url"] + "wp-json/wp/v2/comments?status=hold&per_page=100&_fields=id", headers=auth)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    r["comments_pending"] = len(json.load(resp))
            except Exception as e:
                r["wp_err"] = str(e)[:60]
            try:
                req = urllib.request.Request(site["url"] + "wp-json/fluentform/v1/forms", headers=auth)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    forms = json.load(resp)
                items = forms if isinstance(forms, list) else forms.get("data") or forms.get("forms") or []
                r["leads_total"] = sum(int(f.get("total_Submissions") or f.get("entry_count") or f.get("totalSubmissions") or 0) for f in items)
                r["leads_unread"] = sum(int(f.get("unread_count") or f.get("unreadCount") or 0) for f in items)
            except Exception as e:
                r["ff_err"] = str(e)[:60]
        else:
            r["wp_err"] = "no app password on the Pi yet"
    return r

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    while True:
        cfg = hdconfig.load(); creds = env()
        data = {"ts": int(time.time()), "sites": [check(s, creds) for s in cfg["sites"]]}
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, OUT)
        time.sleep(max(5, int(cfg["intervals"]["sites"])))

if __name__ == "__main__":
    main()
