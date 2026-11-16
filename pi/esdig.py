#!/usr/bin/env python3
"""Pilot missions for the house dashboard: logs into a Floot-built pilot operations portal
(default: Eagle Sight Drone Intelligence Group, operations.eaglesightdroneintelligence.com) with the
pilot's own login and reads /_api/missions - the same call the portal's "My missions" page makes.
Credentials live root-only in /etc/housedash/esdig.env (ESDIG_EMAIL / ESDIG_PASS; set them with
`sudo housedash-add esdig YOUR_EMAIL`). Writes esdig.json; pushes ntfy on a new assignment or a status change.
Read-only: never posts anything to the portal except the login."""
import http.cookiejar, json, os, time, urllib.error, urllib.request

OUT = "/var/lib/housedash/esdig.json"
SEEN = "/var/lib/housedash/esdig_seen.json"   # mission id -> last status, so restarts don't re-alert
ENV = "/etc/housedash/esdig.env"
import hdconfig, notify
UA = {"User-Agent": "housedash/1 (pilot's home dashboard)", "Accept": "application/json"}

# portal status -> (label, dot class). Unknown statuses still show, prettified, as "warn".
STATUS = {
    "assigned": ("Assigned", "warn"), "in_progress": ("In progress", "ok"),
    "complete": ("Complete · awaiting QC", "ok"), "completed": ("Complete · awaiting QC", "ok"), "awaiting_qc": ("Awaiting QC", "ok"),
    "reshoot_required": ("Reshoot required", "bad"), "reshoot": ("Reshoot required", "bad"),
    "verified": ("Verified · awaiting payment", "ok"), "verified_awaiting_payment": ("Verified · awaiting payment", "ok"),
    "paid": ("Paid", "ok"), "cancelled": ("Cancelled", ""), "canceled": ("Cancelled", ""), "not_payable": ("Not payable", ""),
}
DONE = {"paid", "cancelled", "canceled", "not_payable"}   # these drop off the active list (still counted)

def label(st): return STATUS.get(st, (st.replace("_", " ").capitalize(), "warn"))

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

_jar = http.cookiejar.CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))

def _req(url, data=None):
    h = dict(UA)
    if data is not None: h["Content-Type"] = "application/json"
    # Floot speaks superjson: request bodies go as {"json": {...}}, and responses may come back wrapped the same way
    r = urllib.request.Request(url, data=json.dumps({"json": data}).encode() if data is not None else None, headers=h, method="POST" if data is not None else "GET")
    with _op.open(r, timeout=25) as resp: d = json.load(resp)
    return d["json"] if isinstance(d, dict) and set(d) <= {"json", "meta"} and "json" in d else d

def login(base, email, pw):
    _jar.clear()
    _req(base + "/_api/auth/login_with_password", {"email": email, "password": pw})

def missions(base, email, pw):
    try:
        return _req(base + "/_api/missions")
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403): raise
    login(base, email, pw)            # session expired (or first run): log in once and retry
    return _req(base + "/_api/missions")

def slim(m):
    st = (m.get("status") or "").lower()
    lab, cls = label(st)
    return {"id": m.get("id"), "title": m.get("title") or m.get("projectName") or "Mission", "site": m.get("siteCode"),
            "where": m.get("location") or m.get("address") or "", "state": m.get("state"), "status": st, "label": lab, "cls": cls,
            "date": m.get("scheduledFlightDate"), "type": m.get("missionType"), "pkg": m.get("capturePackage"),
            "drive": m.get("driveFolderUrl"), "updated": m.get("updatedAt")}

def load_seen():
    try: return json.load(open(SEEN))
    except Exception: return None

def save_seen(d):
    with open(SEEN + ".tmp", "w") as f: json.dump(d, f)
    os.replace(SEEN + ".tmp", SEEN)

def alerts(ms, base):
    seen = load_seen(); first = seen is None; seen = seen or {}
    for m in ms:
        old = seen.get(m["id"])
        if first: continue                       # first run: learn what's there, don't spam
        if old is None:
            notify.send("esdig_new", f"New mission: {m['title']}", f"{m['where']}{' · fly '+m['date'][:10] if m['date'] else ''} · {m['label']}",
                        priority=4, tags=["helicopter"], click=base, dedupe="esn:" + m["id"], dedupe_s=86400)
        elif old != m["status"]:
            notify.send("esdig_status", f"{m['title']}: {m['label']}", f"was {label(old)[0]}", priority=5 if m["cls"] == "bad" else 3,
                        tags=["warning" if m["cls"] == "bad" else "white_check_mark"], click=base, dedupe=f"ess:{m['id']}:{m['status']}", dedupe_s=86400)
    save_seen({m["id"]: m["status"] for m in ms})

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    while True:
        cfg = hdconfig.load(); e = cfg.get("esdig", {}); base = (e.get("url") or "").rstrip("/")
        creds = env(); out = {"ts": int(time.time()), "url": base}
        if not e.get("on", True):
            out["err"] = "off"
        elif not (creds.get("ESDIG_EMAIL") and creds.get("ESDIG_PASS")):
            out["err"] = "no login on the Pi yet - sudo housedash-add esdig YOUR_EMAIL"
        else:
            try:
                ms = [slim(m) for m in (missions(base, creds["ESDIG_EMAIL"], creds["ESDIG_PASS"]).get("missions") or [])]
                counts = {}
                for m in ms: counts[m["status"]] = counts.get(m["status"], 0) + 1
                active = sorted([m for m in ms if m["status"] not in DONE], key=lambda m: (m["date"] or "9999", m["title"]))
                out.update(ok=True, counts=counts, total=len(ms), active=active,
                           paid=counts.get("paid", 0), cls_counts={k: label(k)[0] for k in counts})
                alerts(ms, base)
            except urllib.error.HTTPError as x:
                out["err"] = f"portal said {x.code}" + (" - check the login" if x.code in (400, 401, 403) else "")
            except Exception as x:
                out["err"] = str(x)[:70]
        with open(OUT + ".tmp", "w") as f: json.dump(out, f)
        os.replace(OUT + ".tmp", OUT)
        time.sleep(max(60, int(cfg["intervals"].get("esdig", 300))))

if __name__ == "__main__":
    main()
