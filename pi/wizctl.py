"""WiZ bulb control over the local UDP protocol (port 38899). Used by setup.py's /api/light and by the
lights-off-when-nobody-home rule in collect.py.

Newer WiZ firmware (1.3x+, replies carry "env":"pro" and an "hmac") rejects unsigned writes with
-32602 "Invalid params". Reads still work. Writes need the home's udp_signing_key (32 hex chars) from the
WiZ app's local-integration export; store it root-only in /etc/housedash/wiz_signing_key (the settings page
does this). Signing: add sigTs to params, HMAC-SHA256 the compact JSON of params with the hex-decoded key,
base64 it into "hmac" and send with "env":"pro"."""
import base64, hashlib, hmac, json, os, socket, time

KEY_FILE = "/etc/housedash/wiz_signing_key"

def signing_key():
    try:
        k = open(KEY_FILE).read().strip().replace(" ", "")
        return bytes.fromhex(k) if k else None
    except Exception:
        return None

def _cmd(ip, method, params, timeout=0.8, sign=False):
    msg = {"method": method, "params": params}
    key = signing_key() if sign else None
    if key:
        params = dict(params, sigTs=int(time.time()))
        body = json.dumps(params, separators=(",", ":"))
        msg = {"method": method, "env": "pro", "params": params,
               "hmac": base64.b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest()).decode()}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    try:
        s.sendto(json.dumps(msg, separators=(",", ":")).encode(), (ip, 38899))
        r = json.loads(s.recv(2048))
        if "error" in r and r["error"].get("code") == -32602 and not key:
            r["hint"] = "this bulb's firmware needs the WiZ signing key - add it on the settings page (lights)"
        return r
    except Exception as e:
        return {"error": str(e)[:60]}
    finally:
        s.close()

def get(ip): return _cmd(ip, "getPilot", {}).get("result", {})

def set_state(ip, on=None, dim=None, temp=None, scene=None):
    p = {}
    if on is not None: p["state"] = bool(on)
    if dim is not None: p["dimming"] = max(10, min(100, int(dim)))
    if temp is not None: p["temp"] = int(temp)          # Kelvin, 2200-6500 for tunable-white bulbs
    if scene is not None: p["sceneId"] = int(scene)      # WiZ scene ids (e.g. 11 warm white, 12 daylight, 6 cozy)
    return _cmd(ip, "setPilot", p, sign=True)

def toggle(ip):
    cur = get(ip)
    return set_state(ip, on=not cur.get("state", False))

def all_lights(lights, on, dim=None):
    return {ip: set_state(ip, on=on, dim=dim) for ip in lights}

def save_key(hexkey):
    k = hexkey.strip().replace(" ", "").lower()
    if k and (len(k) != 32 or any(c not in "0123456789abcdef" for c in k)): raise ValueError("the WiZ signing key is 32 hex characters")
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    with open(os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: f.write(k + "\n")
