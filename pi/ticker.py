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

def build(cfg):
    tk = cfg["ticker"]; by_tag, errs = {}, []
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
    for v in by_tag.values(): v.sort(key=lambda x: -x["ts"])
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
        time.sleep(max(60, int(cfg["intervals"]["ticker"])))

if __name__ == "__main__":
    main()
