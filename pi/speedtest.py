#!/usr/bin/env python3
"""Periodic speed test from the Pi (speedtest-cli, Debian package). Keeps a 30-day history in
/var/lib/housedash/speed.jsonl and a summary in speed.json for the Internet card.
Note: this measures the Pi's own link (a Zero 2 W on Wi-Fi tops out well under a gigabit) - read it as a
trend / outage detector, not as your plan speed. Run by housedash-speedtest.timer."""
import json, os, subprocess, time
import hdconfig

HIST = "/var/lib/housedash/speed.jsonl"
OUT = "/var/lib/housedash/speed.json"

def run():
    r = subprocess.run(["speedtest-cli", "--json", "--secure"], capture_output=True, text=True, timeout=180)
    d = json.loads(r.stdout)
    return {"ts": int(time.time()), "down": round(d["download"] / 1e6, 1), "up": round(d["upload"] / 1e6, 1),
            "ping": round(d["ping"]), "server": (d.get("server") or {}).get("sponsor", "")[:30]}

def main():
    cfg = hdconfig.load()
    if not cfg["speedtest"].get("on", True): return
    try:   # the timer fires every 10 min; honour the configured interval here
        if time.time() - json.load(open(OUT))["ts"] < int(cfg["speedtest"].get("interval_min", 60)) * 60 - 60: return
    except Exception:
        pass
    try:
        rec = run()
    except Exception as e:
        rec = {"ts": int(time.time()), "err": str(e)[:80]}
    with open(HIST, "a") as f: f.write(json.dumps(rec) + "\n")
    cutoff = time.time() - 30 * 86400
    hist = [json.loads(l) for l in open(HIST) if l.strip()]
    hist = [h for h in hist if h["ts"] > cutoff]
    with open(HIST + ".tmp", "w") as f: f.write("".join(json.dumps(h) + "\n" for h in hist))
    os.replace(HIST + ".tmp", HIST)
    good = [h for h in hist if "down" in h]
    last24 = [h for h in good if h["ts"] > time.time() - 86400]
    summary = {"ts": int(time.time()), "last": rec, "n": len(good),
               "spark": [h["down"] for h in good[-48:]],
               "avg24_down": round(sum(h["down"] for h in last24) / len(last24), 1) if last24 else None,
               "min24_down": min((h["down"] for h in last24), default=None),
               "note": "measured from the Pi (Wi-Fi) - a trend, not your plan speed"}
    with open(OUT + ".tmp", "w") as f: json.dump(summary, f)
    os.replace(OUT + ".tmp", OUT)

if __name__ == "__main__":
    main()
