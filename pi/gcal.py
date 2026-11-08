#!/usr/bin/env python3
"""Calendar for the house dashboard, from private iCal feeds (no OAuth, no Google Cloud project).

/etc/housedash/calendars.txt — one calendar per line:   Name | #hexcolor | https://calendar.google.com/calendar/ical/.../private-.../basic.ics
(Google Calendar > the calendar's Settings > "Secret address in iCal format". Color optional.)
Any iCal/ICS URL works: Google, iCloud, Outlook, a public holiday feed. Lines starting with # are ignored.
Writes /var/lib/housedash/calendar.json every 5 min with today's and tomorrow's events."""
import json, os, time, urllib.request
from datetime import datetime, timedelta, date
import icalendar, recurring_ical_events

OUT = "/var/lib/housedash/calendar.json"
CFG = "/etc/housedash/calendars.txt"

def calendars():
    out = []
    try:
        for line in open(CFG):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 2: name, color, url = parts[0], None, parts[1]
            else: name, color, url = parts[0], parts[1] or None, parts[2]
            out.append((name, color, url))
    except FileNotFoundError:
        pass
    return out

def fetch():
    cals = calendars()
    if not cals: return {"ok": False, "err": "no calendars configured"}
    tz = datetime.now().astimezone().tzinfo
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=2)
    events, errs = [], []
    for name, color, url in cals:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "housedash/1"}), timeout=25) as r:
                cal = icalendar.Calendar.from_ical(r.read())
            for ev in recurring_ical_events.of(cal).between(start, end):
                s = ev.get("DTSTART").dt; e = ev.get("DTEND").dt if ev.get("DTEND") else s
                allday = not isinstance(s, datetime)
                if not allday:
                    if s.tzinfo is None: s = s.replace(tzinfo=tz)
                    s = s.astimezone(tz)
                    if isinstance(e, datetime): e = (e.replace(tzinfo=tz) if e.tzinfo is None else e).astimezone(tz)
                events.append({"cal": name, "color": color, "title": str(ev.get("SUMMARY", "(no title)")),
                               "start": s.isoformat(), "end": e.isoformat() if hasattr(e, "isoformat") else None,
                               "allday": allday, "where": str(ev.get("LOCATION", "") or "")})
        except Exception as ex:
            errs.append(f"{name}: {str(ex)[:50]}")
    # de-dupe (same event in two feeds), sort: date, all-day first, then time
    seen, uniq = set(), []
    for ev in events:
        k = (ev["title"], ev["start"][:16])
        if k in seen: continue
        seen.add(k); uniq.append(ev)
    uniq.sort(key=lambda x: (x["start"][:10], not x["allday"], x["start"]))
    return {"ok": True, "events": uniq[:24], "calendars": len(cals), "errs": errs}

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    while True:
        try: data = fetch()
        except Exception as e: data = {"ok": False, "err": str(e)[:80]}
        data["ts"] = int(time.time())
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, OUT)
        time.sleep(300)

if __name__ == "__main__":
    main()
