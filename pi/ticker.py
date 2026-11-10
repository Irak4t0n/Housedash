#!/usr/bin/env python3
"""Bottom-of-screen ticker for the house dashboard: UFC results + hacker / esports / gaming / drone headlines.
Standard library only. Writes /var/lib/housedash/ticker.json every 5 min."""
import html, json, os, re, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

OUT = "/var/lib/housedash/ticker.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux) housedash/1"}
import hdconfig
UFC_API = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"

def get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()

def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()

def rss(tag, name, url, n):
    items = []
    root = ET.fromstring(get(url))
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        if not title: continue
        ts = 0
        try: ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception: pass
        link = clean(it.findtext("link")) or ""
        if not link.startswith("http"):   # some feeds put the URL in guid or in the link element's tail
            g = it.find("guid"); link = clean(g.text) if g is not None and (g.text or "").startswith("http") else ""
        items.append({"tag": tag, "src": name, "text": title, "ts": ts, "url": link})
        if len(items) >= n: break
    return items

def ufc():
    items = []
    d = json.loads(get(UFC_API))
    for ev in d.get("events", []):
        card = ev.get("shortName") or ev.get("name") or "UFC"
        link = next((l.get("href") for l in ev.get("links", []) if l.get("href")), "https://www.espn.com/mma/")
        state = ev.get("status", {}).get("type", {}).get("state")      # pre / in / post
        try: ts = int(datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp())
        except Exception: ts = 0
        comps = ev.get("competitions", [])
        if state == "pre":
            when = datetime.fromtimestamp(ts).strftime("%a %-I:%M %p") if ts else ""
            main = comps[0]["competitors"] if comps else []
            names = " vs ".join(c.get("athlete", {}).get("displayName", "?") for c in main)
            items.append({"tag": "UFC", "src": "ESPN", "text": f"{card} {when} · main event {names}" if names else f"{card} {when}", "ts": ts, "url": link})
            continue
        for c in comps:
            st = c.get("status", {}).get("type", {})
            if st.get("state") != "post": continue
            w = [x for x in c.get("competitors", []) if x.get("winner")]
            l = [x for x in c.get("competitors", []) if not x.get("winner")]
            if not w or not l: continue
            how = st.get("detail") or st.get("shortDetail") or "Final"
            how = re.sub(r"^Final\s*-?\s*", "", how)
            items.append({"tag": "UFC", "src": card, "text": f"{w[0]['athlete']['displayName']} def. {l[0]['athlete']['displayName']}" + (f" · {how}" if how else ""), "ts": ts, "url": link})
    return items[:12]

# ---- league scoreboards (ESPN's public scoreboard JSON): live scores first, then finals, then what's coming up
LEAGUES = {   # tag: (sport/league path, human link)
    "NFL": ("football/nfl", "https://www.espn.com/nfl/scoreboard"),
    "NCAAF": ("football/college-football", "https://www.espn.com/college-football/scoreboard"),
    "NBA": ("basketball/nba", "https://www.espn.com/nba/scoreboard"),
    "NCAAB": ("basketball/mens-college-basketball", "https://www.espn.com/mens-college-basketball/scoreboard"),
    "WNBA": ("basketball/wnba", "https://www.espn.com/wnba/scoreboard"),
    "MLB": ("baseball/mlb", "https://www.espn.com/mlb/scoreboard"),
    "NHL": ("hockey/nhl", "https://www.espn.com/nhl/scoreboard"),
    "MLS": ("soccer/usa.1", "https://www.espn.com/soccer/scoreboard/_/league/usa.1"),
    "EPL": ("soccer/eng.1", "https://www.espn.com/soccer/scoreboard/_/league/eng.1"),
    "UCL": ("soccer/uefa.champions", "https://www.espn.com/soccer/scoreboard/_/league/uefa.champions"),
}
SPORTS_NEWS = "https://www.espn.com/espn/rss/news"
DOT = " · "
_live = [False]   # any game in progress -> the loop refreshes every minute instead of every five

def scoreboard(tag, n=8):
    path, page = LEAGUES[tag]
    d = json.loads(get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"))
    live, final, soon = [], [], []
    now = time.time()
    for ev in d.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]; teams = comp.get("competitors", [])
        if len(teams) != 2: continue
        st = (comp.get("status") or ev.get("status") or {}).get("type", {}); state = st.get("state")
        try: ts = int(datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp())
        except Exception: ts = 0
        away = next((t for t in teams if t.get("homeAway") == "away"), teams[0]); home = next((t for t in teams if t.get("homeAway") == "home"), teams[1])
        def nm(t):
            team = t.get("team", {}); rank = (t.get("curatedRank") or {}).get("current")
            return (f"#{rank} " if rank and rank <= 25 else "") + (team.get("abbreviation") or team.get("shortDisplayName") or "?")
        link = next((l.get("href") for l in ev.get("links", []) if l.get("href")), page)
        if state == "pre":
            if ts - now > 4 * 86400: continue
            when = datetime.fromtimestamp(ts).strftime("%a %-I:%M %p") if ts else ""
            tv = next((b for x in comp.get("broadcasts", []) for b in x.get("names", [])), "")
            soon.append({"tag": tag, "src": "ESPN", "text": f"{nm(away)} @ {nm(home)}{DOT}{when}" + (f"{DOT}{tv}" if tv else ""), "ts": ts, "url": link})
        else:
            detail = st.get("shortDetail") or st.get("detail") or ("Final" if state == "post" else "Live")
            text = f"{nm(away)} {away.get('score', '')}  {nm(home)} {home.get('score', '')}{DOT}{detail}"
            (live if state == "in" else final).append({"tag": tag, "src": "LIVE" if state == "in" else "ESPN", "text": text, "ts": ts, "url": link})
    if live: _live[0] = True
    soon.sort(key=lambda x: x["ts"]); final.sort(key=lambda x: -x["ts"])
    return (live + final + soon)[:n]

# ---- fun facts: "on this day" from Wikipedia plus a few random facts; cached an hour so the wire isn't a new set every minute
_facts = {"t": 0, "items": []}
def facts(n=8):
    if time.time() - _facts["t"] < 3600 and _facts["items"]: return _facts["items"]
    out, now = [], datetime.now()
    try:
        d = json.loads(get(f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/{now.month:02d}/{now.day:02d}"))
        grim = re.compile(r"kill|died|death|dead|massacre|war\b|bomb|crash|assassin|murder|attack|disaster|earthquake|derail|shot|execut|genocide|terror|riot|famine|sank|explo|hijack|fire\b", re.I)
        picks = [e for e in d.get("selected", []) if not grim.search(e.get("text", ""))]   # it's the living-room TV: keep the history light
        for e in picks[: (n + 1) // 2]:
            page = (e.get("pages") or [{}])[0]
            out.append({"tag": "FACT", "src": "On this day", "text": f"On this day in {e.get('year')}: {clean(e.get('text'))}", "ts": 0,
                        "url": ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")})
    except Exception:
        pass
    for _ in range(n - len(out)):
        try:
            f = json.loads(get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en", timeout=8))
            t = clean(f.get("text"))
            if t and len(t) < 220: out.append({"tag": "FACT", "src": "Fun fact", "text": "Did you know? " + t, "ts": 0, "url": f.get("source_url") or ""})
        except Exception:
            break
    if out: _facts.update(t=time.time(), items=out)
    return out or _facts["items"]

def build(cfg):
    tk = cfg["ticker"]; by_tag, errs = {}, []
    _live[0] = False
    if tk.get("facts", True):
        try:
            got = facts(int(tk.get("facts_n", 8)))
            if got: by_tag["FACT"] = list(got)
        except Exception as e:
            errs.append(f"facts: {str(e)[:40]}")
    lg = tk.get("leagues", {})
    for tag in LEAGUES:
        if not lg.get(tag, True): continue
        try:
            got = scoreboard(tag, int(tk.get("games_per_league", 8)))
            if got: by_tag[tag] = got
        except Exception as e:
            errs.append(f"{tag}: {str(e)[:40]}")
    if tk.get("sports_news", True):
        try: by_tag["SPORTS"] = rss("SPORTS", "ESPN", SPORTS_NEWS, int(tk.get("sports_news_n", 10)))
        except Exception as e: errs.append(f"sports news: {str(e)[:40]}")
    scores = set(by_tag) - {"FACT"}   # keep the order scoreboards came in (live, final, upcoming) - don't re-sort those by time
    if tk.get("ufc", True):
        try:
            for it in ufc(): by_tag.setdefault("UFC", []).append(it)
        except Exception as e:
            errs.append(f"ufc: {str(e)[:40]}")
    for f in tk["feeds"]:
        if not f.get("on", True): continue
        tag, name, url, n = f.get("tag", "NEWS"), f["name"], f["url"], int(f.get("n", 5))
        try:
            for it in rss(tag, name, url, n): by_tag.setdefault(tag, []).append(it)
        except Exception as e:
            errs.append(f"{name}: {str(e)[:40]}")
    for t, v in by_tag.items():
        if t not in scores or t == "SPORTS": v.sort(key=lambda x: -x["ts"])
    # interleave categories so the ticker keeps changing flavor
    order = list(tk.get("order", [])) + [t for t in by_tag if t not in tk.get("order", [])]
    out, seen = [], set()
    while any(by_tag.get(t) for t in order):
        for t in order:
            if by_tag.get(t):
                it = by_tag[t].pop(0)
                k = it["text"].lower()[:60]
                if k in seen: continue
                seen.add(k); out.append(it)
    out = out[:int(tk.get("max_items", 70))]
    return {"ok": True, "items": out, "errs": errs}

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    while True:
        cfg = hdconfig.load()
        try: data = build(cfg)
        except Exception as e: data = {"ok": False, "err": str(e)[:80]}
        data["ts"] = int(time.time())
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, OUT)
        time.sleep(60 if _live[0] else max(60, int(cfg["intervals"]["ticker"])))   # games in progress: refresh scores every minute

if __name__ == "__main__":
    main()
