#!/usr/bin/env python3
"""LAN honeypot ("canary"). Listens on ports nothing legitimate should ever connect to on this Pi
(telnet, ftp, SMB, RDP, VNC, MSSQL by default). Any connection is logged with source, port and the first
bytes sent, written to /var/lib/housedash/security.json (shared with audit.py) and pushed as a notification.
Standard library only; runs as root (low ports). Config: security.canary_ports."""
import asyncio, json, os, time
import hdconfig, notify

SEC = "/var/lib/housedash/security.json"
BANNERS = {21: b"220 FTP Server ready.\r\n", 23: b"\xff\xfd\x18\xff\xfd\x20\xff\xfd\x23\xff\xfd\x27login: ", 25: b"220 mail ESMTP\r\n",
           110: b"+OK POP3 ready\r\n", 1433: b"", 3389: b"", 445: b"", 5900: b"RFB 003.008\n", 6379: b"", 8080: b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm=admin\r\n\r\n"}
NAMES = {21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 110: "pop3", 445: "smb", 1433: "mssql", 3306: "mysql", 3389: "rdp", 5900: "vnc", 6379: "redis", 8080: "http-alt"}

def load():
    try: return json.load(open(SEC))
    except Exception: return {"canary": [], "audit": {}}

def save(d):
    os.makedirs(os.path.dirname(SEC), exist_ok=True)
    with open(SEC + ".tmp", "w") as f: json.dump(d, f)
    os.replace(SEC + ".tmp", SEC)

async def handle(reader, writer, port):
    peer = writer.get_extra_info("peername") or ("?", 0)
    try:
        if BANNERS.get(port): writer.write(BANNERS[port]); await writer.drain()
        try: data = await asyncio.wait_for(reader.read(200), timeout=6)
        except asyncio.TimeoutError: data = b""
    except Exception:
        data = b""
    finally:
        try: writer.close()
        except Exception: pass
    hit = {"ts": int(time.time()), "src": peer[0], "port": port, "svc": NAMES.get(port, str(port)),
           "data": data[:80].decode("utf-8", "replace").replace("\r", "\\r").replace("\n", "\\n")}
    d = load(); d.setdefault("canary", []).append(hit); d["canary"] = d["canary"][-200:]; d["canary_ts"] = hit["ts"]; save(d)
    notify.send("canary", f"Honeypot hit: {hit['svc']} from {hit['src']}", f"port {port}" + (f" · sent: {hit['data'][:60]}" if hit["data"] else ""),
                priority=4, tags=["honey_pot"], dedupe=f"canary:{hit['src']}:{port}", dedupe_s=1800)

async def main():
    ports = hdconfig.load()["security"]["canary_ports"]
    servers = []
    for p in ports:
        try:
            srv = await asyncio.start_server(lambda r, w, p=p: handle(r, w, p), "0.0.0.0", p)
            servers.append(srv)
        except Exception as e:
            print(f"port {p}: {e}")
    d = load(); d["canary_ports"] = [s.sockets[0].getsockname()[1] for s in servers if s.sockets]; d.setdefault("canary", []); save(d)
    await asyncio.gather(*(s.serve_forever() for s in servers))

if __name__ == "__main__":
    asyncio.run(main())
