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

def fingerprint(html):
    # strip the bits that change on every load (nonces, timestamps, csrf) and hash what's left
    h = re.sub(rb'(nonce|_wpnonce|csrf|token)[=":\s]*[A-Za-z0-9+/=_-]+', b"", html)
    h = re.sub(rb"\d{10,}", b"", h)
    h = re.sub(rb"email-protection#[0-9a-f]+", b"email-protection", h)        # Cloudflare email obfuscation rotates per load
    h = re.sub(rb'data-cfemail="[0-9a-f]+"', b"", h)
    h = re.sub(rb"[?&](ver|v|_|t|cb|nocache)=[^\"'&\s]+", b"", h)             # cache-busters
    h = re.sub(rb"<script[^>]*>.*?</script>", b"", h, flags=re.S)             # inline scripts carry nonces/ids
    h = re.sub(rb"<!--.*?-->", b"", h, flags=re.S)
    h = re.sub(rb"\s+", b" ", h)
    return hashlib.sha256(h).hexdigest()[:16]

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
            fp = fingerprint(body)
            r["fingerprint"] = fp
            prev = _last_fp.get(site["key"])
            r["changed"] = bool(prev and prev != fp)
            _last_fp[site["key"]] = fp
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
    if r.get("changed"): notify.send("site_changed", f"{site['name']}: home page changed", "content fingerprint differs from the last check - look at it", priority=4, tags=["eyes"], dedupe="chg:" + k, dedupe_s=21600)
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
