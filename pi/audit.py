#!/usr/bin/env python3
"""Nightly LAN audit: nmap the subnet, keep the last result, diff against it (new hosts, new open ports,
ports that closed) and record the diff in /var/lib/housedash/security.json + a notification when something
changed. Run by housedash-audit.timer (hour from config security.audit_hour). Needs nmap (apt)."""
import json, os, re, subprocess, time, xml.etree.ElementTree as ET
import hdconfig, notify

SEC = "/var/lib/housedash/security.json"
LAST = "/var/lib/housedash/audit-last.json"

def subnet():
    cfg = hdconfig.load()["security"]
    if cfg.get("audit_range"): return cfg["audit_range"]
    out = subprocess.run(["ip", "-4", "-o", "route", "show", "scope", "link"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)\s+dev\s+(wlan0|eth0|end0)", out) or re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)", out)
    return m.group(1) if m else "192.168.1.0/24"

def scan(rng):
    xml = subprocess.run(["nmap", "-sS", "-T4", "--top-ports", "300", "--open", "-n", "-oX", "-", rng], capture_output=True, text=True, timeout=3000).stdout
    root = ET.fromstring(xml); hosts = {}
    for h in root.iter("host"):
        if h.find("status").get("state") != "up": continue
        ip = next((a.get("addr") for a in h.iter("address") if a.get("addrtype") == "ipv4"), None)
        mac = next((a.get("addr") for a in h.iter("address") if a.get("addrtype") == "mac"), "")
        vendor = next((a.get("vendor") for a in h.iter("address") if a.get("addrtype") == "mac"), "") or ""
        ports = sorted(int(p.get("portid")) for p in h.iter("port") if p.find("state").get("state") == "open")
        hosts[ip] = {"mac": mac, "vendor": vendor, "ports": ports}
    return hosts

def main():
    import sys
    cfg = hdconfig.load()["security"]
    if "--now" not in sys.argv and time.localtime().tm_hour != int(cfg.get("audit_hour", 3)): return   # hourly timer, run at the configured hour
    rng = subnet(); t0 = time.time()
    try: hosts = scan(rng)
    except Exception as e:
        d = _load(); d["audit"] = {"ts": int(time.time()), "err": str(e)[:80], "range": rng}; _save(d); return
    try: prev = json.load(open(LAST))
    except Exception: prev = None
    diff = {"new_hosts": [], "gone_hosts": [], "new_ports": [], "closed_ports": []}
    if prev is not None:
        for ip, h in hosts.items():
            if ip not in prev: diff["new_hosts"].append({"ip": ip, "vendor": h["vendor"], "ports": h["ports"]})
            else:
                for p in h["ports"]:
                    if p not in prev[ip]["ports"]: diff["new_ports"].append({"ip": ip, "port": p})
                for p in prev[ip]["ports"]:
                    if p not in h["ports"]: diff["closed_ports"].append({"ip": ip, "port": p})
        for ip in prev:
            if ip not in hosts: diff["gone_hosts"].append(ip)
    json.dump(hosts, open(LAST + ".tmp", "w")); os.replace(LAST + ".tmp", LAST)
    exposed = sorted(((ip, h["ports"]) for ip, h in hosts.items() if h["ports"]), key=lambda x: -len(x[1]))
    d = _load()
    d["audit"] = {"ts": int(time.time()), "range": rng, "hosts": len(hosts), "secs": int(time.time() - t0), "diff": diff,
                  "first": prev is None, "top": [{"ip": ip, "ports": p[:12], "vendor": hosts[ip]["vendor"][:24]} for ip, p in exposed[:8]]}
    _save(d)
    changes = len(diff["new_hosts"]) + len(diff["new_ports"])
    if changes:
        lines = [f"new host {x['ip']} ({x['vendor'] or '?'}) ports {x['ports'][:6]}" for x in diff["new_hosts"]] + [f"{x['ip']} opened {x['port']}" for x in diff["new_ports"]]
        notify.send("audit", f"LAN audit: {changes} change(s)", "\n".join(lines[:8]), priority=4, tags=["mag"])

def _load():
    try: return json.load(open(SEC))
    except Exception: return {"canary": [], "audit": {}}
def _save(d):
    with open(SEC + ".tmp", "w") as f: json.dump(d, f)
    os.replace(SEC + ".tmp", SEC)

if __name__ == "__main__":
    main()
