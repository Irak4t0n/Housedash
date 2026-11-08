"""WiZ bulb control over the local UDP protocol (port 38899). Used by setup.py's /api/light and by the
lights-off-when-nobody-home rule in collect.py."""
import json, socket

def _cmd(ip, method, params, timeout=0.8):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    try:
        s.sendto(json.dumps({"method": method, "params": params}).encode(), (ip, 38899))
        return json.loads(s.recv(1024))
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
    return _cmd(ip, "setPilot", p)

def toggle(ip):
    cur = get(ip)
    return set_state(ip, on=not cur.get("state", False))

def all_lights(lights, on, dim=None):
    return {ip: set_state(ip, on=on, dim=dim) for ip in lights}
