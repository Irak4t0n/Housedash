#!/usr/bin/env python3
"""Anycubic LAN-mode watcher. Keeps an MQTT session to each printer (anycubic-lan)
and writes /var/lib/housedash/printers.json for collect.py. Re-handshakes when a
printer reboots (its broker credentials rotate) or goes quiet.

- A dropped connection keeps the last known values on the card (marked stale with
  its age) instead of blanking the printer to "LAN mode off".
- Watchdog: if no report arrives for STALE_S while connected, the session is torn
  down and re-handshaked (paho's own auto-reconnect can't recover rotated credentials).
- A finished print is detected however the printer reports it: job status COMPLETE,
  or printing -> free/idle with the job at >= 97%. The print is logged here (prints.jsonl)
  and pushed once. Times from the printer (elapsed/remaining) are minutes."""
import asyncio, json, os, time
import aiohttp
from anycubic_lan import AnycubicLanClient, handshake, AnycubicLanError

OUT = "/var/lib/housedash/printers.json"
PRINT_LOG = "/var/lib/housedash/prints.jsonl"   # one line per finished print, kept forever (collect.py counts it)
STALE_S = 150                                     # no report this long while "connected" -> reconnect
import hdconfig, notify
PRINTERS = hdconfig.load()["printers"]   # restart housedash-printers after changing printers in config
state = {}   # ip -> dict for the page
ACTIVE = ("printing", "paused", "preheating", "levelling", "downloading", "checking", "slicing")

def write():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f: json.dump({"ts": int(time.time()), "printers": state}, f)
    os.replace(tmp, OUT)

def log_print(ip, name, file, minutes):
    with open(PRINT_LOG, "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "ip": ip, "printer": name, "file": file, "secs": int((minutes or 0) * 60)}) + "\n")

async def watch(ip, name):
    state[ip] = {"name": name, "ip": ip, "lan": False}
    while True:
        last = {"t": time.time()}
        try:
            async with aiohttp.ClientSession() as s:
                info = await handshake(s, ip)
            client = AnycubicLanClient(info)
            def on_state(st):
                last["t"] = time.time()
                job, t = st.job, st.temperatures
                ps = str(st.printer_state or "").lower()
                # job.print_status is PRINTING/COMPLETE/CANCELLED/...; the page wants one word
                if job is not None and job.print_status is not None:
                    js = job.print_status.name.lower()
                    if js == "printing" and job.pause: js = "paused"
                    if js in ACTIVE: ps = js
                    elif js == "complete": ps = "finished"
                    elif js == "cancelled": ps = "cancelled"
                old = state.get(ip) or {}
                prev = old.get("state")
                # some firmware never says COMPLETE: the job just vanishes and the printer goes back to free
                if prev == "printing" and ps in ("free", "idle", "") and (old.get("progress") or 0) >= 97: ps = "finished"
                if prev and prev != ps:
                    fn = (job.filename if job else None) or old.get("file") or ""
                    if ps == "finished" and prev in ACTIVE:
                        log_print(ip, name, fn, (job.print_time if job else None) or old.get("elapsed"))
                        notify.send("print_done", f"{name}: print finished", fn, tags=["white_check_mark"], dedupe=f"done:{ip}:{fn}", dedupe_s=600)
                    elif ps == "error" or "error" in ps: notify.send("print_error", f"{name}: printer error", f"{fn} ({ps})", priority=4, tags=["warning"], dedupe=f"err:{ip}", dedupe_s=1800)
                    elif ps == "paused" and prev == "printing": notify.send("print_error", f"{name}: paused", fn, dedupe=f"pause:{ip}", dedupe_s=1800)
                if ps in ("free", "idle") and prev == "finished": ps = "finished"   # stay "finished" (with the file) until a new job starts
                keep = ps == "finished" and job is None
                state[ip] = {"name": name, "ip": ip, "lan": True, "model": st.model, "firmware": st.firmware_version,
                             "state": ps, "nozzle": t.nozzle, "nozzle_target": t.nozzle_target, "bed": t.bed, "bed_target": t.bed_target,
                             "chamber": t.chamber, "updated": int(time.time()),
                             "progress": job.progress if job else (100 if keep else None),
                             "file": job.filename if job else (old.get("file") if keep else None),
                             "layer": job.current_layer if job else None, "layers": job.total_layers if job else None,
                             "elapsed": job.print_time if job else None, "remaining": job.remain_time if job else None}
                write()
            client.add_state_listener(on_state)
            await client.connect()
            try:
                while True:
                    await asyncio.sleep(30)
                    await client.query_all()
                    if time.time() - last["t"] > STALE_S: raise TimeoutError(f"no report for {int(time.time() - last['t'])} s")
            finally:
                try: await client.disconnect()
                except Exception: pass
        except AnycubicLanError as e:
            err = type(e).__name__
        except Exception as e:
            err = str(e)[:60] or type(e).__name__
        st = state.setdefault(ip, {"name": name, "ip": ip, "lan": False})
        st["err"] = err; st["stale"] = True          # keep the last values; the page shows them with their age
        write()
        await asyncio.sleep(20)

async def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    await asyncio.gather(*(watch(ip, n) for ip, n in PRINTERS.items()))

if __name__ == "__main__":
    asyncio.run(main())
