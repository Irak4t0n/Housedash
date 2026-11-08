#!/usr/bin/env python3
"""Anycubic LAN-mode watcher. Keeps an MQTT session to each printer (anycubic-lan)
and writes /var/lib/housedash/printers.json for collect.py. Re-handshakes when a
printer reboots (its broker credentials rotate) or drops off."""
import asyncio, json, os, time
import aiohttp
from anycubic_lan import AnycubicLanClient, handshake, AnycubicLanError

OUT = "/var/lib/housedash/printers.json"
import hdconfig
PRINTERS = hdconfig.load()["printers"]   # restart housedash-printers after changing printers in config
state = {}   # ip -> dict for the page

def write():
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f: json.dump({"ts": int(time.time()), "printers": state}, f)
    os.replace(tmp, OUT)

async def watch(ip, name):
    while True:
        state[ip] = {"name": name, "ip": ip, "lan": False}
        write()
        try:
            async with aiohttp.ClientSession() as s:
                info = await handshake(s, ip)
            client = AnycubicLanClient(info)
            def on_state(st):
                job, t = st.job, st.temperatures
                ps = str(st.printer_state or "").lower()
                # job.print_status is PRINTING/COMPLETE/CANCELLED/...; the page wants one word
                if job is not None and job.print_status is not None:
                    js = job.print_status.name.lower()
                    if js == "printing" and job.pause: js = "paused"
                    if js in ("printing", "paused", "preheating", "levelling", "downloading", "checking", "slicing"): ps = js
                    elif js == "complete": ps = "finished"
                    elif js == "cancelled": ps = "cancelled"
                state[ip] = {"name": name, "ip": ip, "lan": True, "model": st.model, "firmware": st.firmware_version,
                             "state": ps, "nozzle": t.nozzle, "nozzle_target": t.nozzle_target, "bed": t.bed, "bed_target": t.bed_target,
                             "chamber": t.chamber,
                             "progress": job.progress if job else None, "file": job.filename if job else None,
                             "layer": job.current_layer if job else None, "layers": job.total_layers if job else None,
                             "elapsed": job.print_time if job else None, "remaining": job.remain_time if job else None}
                write()
            client.add_state_listener(on_state)
            await client.connect()
            while True:
                await asyncio.sleep(30)
                await client.query_all()
        except AnycubicLanError as e:
            state[ip]["err"] = type(e).__name__
        except Exception as e:
            state[ip]["err"] = str(e)[:60]
        write()
        await asyncio.sleep(60)

async def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    await asyncio.gather(*(watch(ip, n) for ip, n in PRINTERS.items()))

asyncio.run(main())
