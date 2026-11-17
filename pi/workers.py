#!/opt/housedash/venv/bin/python
"""Runs the dashboard's always-on collectors as threads in ONE Python process instead of seven.
Each one was its own interpreter (~10-15 MB apiece); on a 512 MB Pi Zero 2 W that pushed the system into
constant swapping until it stalled and the hardware watchdog reset it. Same code, same loops, same files -
just shared memory. A collector that crashes is restarted after 30 s without touching the others.

Runs under the venv's python (gcal needs icalendar, printers needs anycubic-lan; the rest is stdlib).
The old unit names (housedash-collect, -sites, -calendar, ...) are aliases of housedash-workers, so
`systemctl restart housedash-sites` from the settings page or housedash-add restarts this process."""
import asyncio, importlib, sys, threading, time, traceback

sys.path.insert(0, "/opt/housedash")
JOBS = ["collect", "outdoors", "sites", "ticker", "gcal", "esdig", "printers"]

def run(name):
    while True:
        try:
            mod = importlib.import_module(name)
            r = mod.main()
            if asyncio.iscoroutine(r): asyncio.run(r)          # printers.main() is async
            print(f"[workers] {name}: main() returned", flush=True)
        except Exception:
            print(f"[workers] {name} crashed:", flush=True); traceback.print_exc()
        time.sleep(30)

if __name__ == "__main__":
    for n in JOBS:
        threading.Thread(target=run, args=(n,), name=n, daemon=True).start()
        time.sleep(2)                                           # stagger start-up (first fetches, handshakes)
    print(f"[workers] running: {', '.join(JOBS)}", flush=True)
    while True: time.sleep(3600)
