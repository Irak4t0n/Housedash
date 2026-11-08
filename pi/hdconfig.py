"""Shared config for the house dashboard. /etc/housedash/config.json overrides DEFAULTS (deep-merged),
so a fresh install works with nothing in it and the setup page only has to store what changed.
Every service calls load() each loop (cheap: one small file) so edits apply within seconds."""
import copy, json, os

PATH = "/etc/housedash/config.json"

DEFAULTS = {
    "title": "HOME",
    "intervals": {"collect": 10, "page": 5, "sites": 20, "internet": 10, "outdoors": 600, "ticker": 300},
    "alerts": {"quiet_start": 22, "quiet_end": 7, "return_gap_h": 2, "return_cooldown_h": 6,
               "sound_new": True, "sound_return": True, "banner_new_s": 20, "banner_return_s": 12},
    # card order + visibility; ids are fixed, labels are what the TV shows
    "cards": [
        {"id": "wx", "label": "Weather", "on": True}, {"id": "tide", "label": "Tide", "on": True},
        {"id": "fish", "label": "What's biting", "on": True}, {"id": "lan", "label": "Devices", "on": True, "tall": True},
        {"id": "pihole", "label": "Pi-hole", "on": True}, {"id": "pr", "label": "Printers", "on": True},
        {"id": "wg", "label": "VPN", "on": True}, {"id": "wiz", "label": "Lights", "on": True},
        {"id": "net", "label": "Internet · Pi", "on": True}, {"id": "sites", "label": "Websites", "on": True},
        {"id": "cal", "label": "Calendar", "on": True},
    ],
    "grid": {"columns": 4, "rows": 3},
    "ticker": {
        "on": True, "speed": 95, "max_items": 70, "ufc": True, "label": "WIRE",
        "tags": {"UFC": "#ff8a8a", "HACK": "#3ddc84", "ESPORTS": "#d7a6ff", "GAMING": "#4cc2ff", "HN": "#ffb020", "DRONE": "#7fe0ff", "NEWS": "#e6edf3"},
        "order": ["UFC", "HACK", "ESPORTS", "GAMING", "HN", "DRONE", "NEWS"],
        "feeds": [
            {"on": True, "tag": "UFC", "name": "ESPN MMA", "url": "https://www.espn.com/espn/rss/mma/news", "n": 4},
            {"on": True, "tag": "ESPORTS", "name": "HLTV", "url": "https://www.hltv.org/rss/news", "n": 5},
            {"on": True, "tag": "ESPORTS", "name": "Dexerto", "url": "https://www.dexerto.com/feed/", "n": 6},
            {"on": True, "tag": "GAMING", "name": "PC Gamer", "url": "https://www.pcgamer.com/rss/", "n": 6},
            {"on": True, "tag": "GAMING", "name": "IGN", "url": "https://feeds.feedburner.com/ign/games-all", "n": 4},
            {"on": True, "tag": "GAMING", "name": "Kotaku", "url": "https://kotaku.com/rss", "n": 4},
            {"on": True, "tag": "HACK", "name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews", "n": 6},
            {"on": True, "tag": "HACK", "name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/", "n": 6},
            {"on": True, "tag": "HACK", "name": "Hackaday", "url": "https://hackaday.com/blog/feed/", "n": 5},
            {"on": True, "tag": "HN", "name": "Hacker News", "url": "https://hnrss.org/frontpage", "n": 8},
            {"on": True, "tag": "DRONE", "name": "DroneDJ", "url": "https://dronedj.com/feed/", "n": 5},
        ],
    },
    # any US point works; the NWS grid is looked up from lat/lon automatically
    "weather": {"label": "Foley AL", "lat": 30.4066, "lon": -87.6836, "fly_good_mph": 12, "fly_ok_mph": 18},
    "tides": {"station": "8731269", "label": "Gulf State Park Pier", "water_temp_station": "8735180"},
    "printers": {},     # ip: name  (Anycubic printers in LAN mode)
    "lights": {},       # ip: name  (WiZ bulbs)
    "hosts": {},        # ip: name  (devices whose presence to show)
    "network": {"ddns_host": "", "pi_ip": "", "setup_port": 8089,
                "pihole_password_file": "/etc/housedash/pihole_password", "pihole_api": "http://127.0.0.1/api", "wg_interface": "wg0"},
    "wg_names": {},     # tunnel ip: name  (WireGuard peers)
    "sites": [],        # [{"key","name","url","wp"}]  websites to watch
}

def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base and isinstance(base[k], dict) and isinstance(v, dict) else v
        return out
    return over

_cache = {"mtime": None, "cfg": None}

def load():
    """Full effective config (defaults + overrides). Cached by file mtime."""
    try:
        m = os.stat(PATH).st_mtime
    except FileNotFoundError:
        m = None
    if _cache["cfg"] is None or _cache["mtime"] != m:
        over = {}
        if m is not None:
            try:
                with open(PATH) as f: over = json.load(f)
            except Exception:
                over = {}
        _cache["cfg"] = _merge(copy.deepcopy(DEFAULTS), over)
        _cache["mtime"] = m
    return _cache["cfg"]

def overrides():
    try:
        with open(PATH) as f: return json.load(f)
    except Exception:
        return {}

def save(over):
    """Write overrides (what differs from DEFAULTS is the caller's business; we just store the dict)."""
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f: json.dump(over, f, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, PATH)
    _cache["cfg"] = None
