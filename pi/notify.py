"""Push notifications for the house dashboard via ntfy (https://ntfy.sh or your own ntfy server).
Config section "notify": {"on", "server", "topic", "events": {event: bool}, "quiet_respect": bool}.
Any service calls notify.send(event, title, message, ...) - it's a no-op if that event is off.
Also appends every notification to /var/lib/housedash/notify.log (shown in the dashboard's phone view)."""
import json, os, time, urllib.request
import hdconfig

LOG = "/var/lib/housedash/notify.log"
_recent = {}   # dedupe key -> ts

def _quiet(c):
    a = c["alerts"]; h = time.localtime().tm_hour; s, e = int(a["quiet_start"]), int(a["quiet_end"])
    return (h >= s or h < e) if s > e else (s <= h < e)

def send(event, title, message, priority=3, tags=None, click=None, dedupe=None, dedupe_s=3600):
    """priority 1-5 (ntfy). dedupe: a key that suppresses repeats for dedupe_s seconds."""
    c = hdconfig.load(); n = c.get("notify", {})
    if dedupe:
        now = time.time()
        if now - _recent.get(dedupe, 0) < dedupe_s: return False
        _recent[dedupe] = now
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f: f.write(json.dumps({"ts": int(time.time()), "event": event, "title": title, "msg": message}) + "\n")
    except Exception:
        pass
    if not n.get("on") or not n.get("topic") or not n.get("events", {}).get(event, True): return False
    if n.get("quiet_respect", True) and _quiet(c) and priority < 4: priority = 2   # quiet hours: deliver silently
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": str(priority), "User-Agent": "housedash/1"}
    if tags: headers["Tags"] = ",".join(tags)
    if click: headers["Click"] = click
    try:
        req = urllib.request.Request(n["server"].rstrip("/") + "/" + n["topic"], data=message.encode(), headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False

def recent(n=30):
    try:
        lines = open(LOG).read().splitlines()[-n:]
        return [json.loads(l) for l in lines][::-1]
    except Exception:
        return []
