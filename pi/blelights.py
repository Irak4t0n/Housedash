#!/usr/bin/env python3
"""Bluetooth LED controllers: Zengge / Magic Home (LEDnetWF*) and Magic Lantern / duoCo (ELK-BLEDOM, MELK-*, LEDBLE, MODELX).

The Pi's own Bluetooth radio talks to them, so they need no Wi-Fi, no cloud and no app. Protocols are
transcribed from the Home Assistant integrations lednetwf_ble (8none1) and elkbledom (dave-code-ruiz).

CLI (run with the housedash venv python, which has bleak):
    blelights.py scan [seconds]                 -> JSON list of controllers in range
    blelights.py on|off  <mac> [kind]
    blelights.py color   <mac> R G B [bright%] [kind]
    blelights.py bright  <mac> <percent> [kind]
    blelights.py white   <mac> [percent] [kind]
    blelights.py blink   <mac> [kind]           -> red / blue / white so you can see which one it is
kind is lednetwf or elk; it is worked out from the name when omitted (scan results carry it).
"""
import asyncio, colorsys, json, sys, time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover
    BleakClient = BleakScanner = None

STATE = "/var/lib/housedash/ble.json"

# ---- which controller is which -------------------------------------------------------------------
ELK_PREFIXES = ("ELK-BLEDOM", "ELK-BLEDOB", "ELK-BULB", "MELK", "LEDBLE", "MODELX", "ELK-")
LEDNET_PREFIXES = ("LEDNETWF", "LEDNET")


def kind_of(name):
    n = (name or "").upper()
    if n.startswith(LEDNET_PREFIXES):
        return "lednetwf"
    if n.startswith(ELK_PREFIXES):
        return "elk"
    return None


# ---- Zengge / LEDnetWF -------------------------------------------------------------------------------
LEDNET_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"
_seq = [int(time.time()) & 0xFF]


def _wrap(raw, family=0x0B):
    """8-byte transport header: ver, seq, frag 0x80 0x00, len(2), len+1, cmd family."""
    _seq[0] = (_seq[0] + 1) & 0xFF
    n = len(raw)
    return bytes([0x00, _seq[0], 0x80, 0x00, (n >> 8) & 0xFF, n & 0xFF, (n + 1) & 0xFF, family]) + bytes(raw)


def _ck(raw):
    return bytes(raw) + bytes([sum(raw) & 0xFF])


def lednet_power(on):
    return _wrap(_ck([0x3B, 0x23 if on else 0x24, 0, 0, 0, 0, 0, 0, 0, 0x32, 0, 0]))


def lednet_color(r, g, b, bright=100):
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    packed = (int(h * 360) << 7) | int(s * 100)
    bright = max(1, min(100, int(bright)))
    return _wrap(_ck([0x3B, 0xA1, (packed >> 8) & 0xFF, packed & 0xFF, bright, 0, 0, r & 0xFF, g & 0xFF, b & 0xFF, 0, 0]))


def lednet_color_simple(r, g, b, ww=0, cw=0):
    """0x31 format for the plain RGB/RGBW controllers that ignore 0x3B colour."""
    mode = 0x5A if (r or g or b) and (ww or cw) else (0x0F if (ww or cw) else 0xF0)
    return _wrap(_ck([0x31, r & 0xFF, g & 0xFF, b & 0xFF, ww & 0xFF, cw & 0xFF, mode, 0x0F]))


def lednet_bright(pct):
    pct = max(0, min(100, int(pct)))
    return _wrap(_ck([0x3B, 0x01, 0, 0, pct, 0, pct, 0, 0, 0, 0, 0]))


# ---- Magic Lantern / ELK-BLEDOM / MELK ------------------------------------------------------------
ELK_WRITE = "0000fff3-0000-1000-8000-00805f9b34fb"
ELK_LOGIN = (bytes([0x7E, 0x07, 0x83]), bytes([0x7E, 0x04, 0x04]))


def elk_power(on):
    return bytes([0x7E, 0x04, 0x04, 0xF0 if on else 0x00, 0x00, 0x01 if on else 0x00, 0xFF, 0x00, 0xEF])


def elk_color(r, g, b):
    return bytes([0x7E, 0x00, 0x05, 0x03, r & 0xFF, g & 0xFF, b & 0xFF, 0x00, 0xEF])


def elk_bright(pct):
    return bytes([0x7E, 0x04, 0x01, max(0, min(100, int(pct))), 0x01, 0xFF, 0xFF, 0x00, 0xEF])


def elk_white(pct):
    return bytes([0x7E, 0x07, 0x05, 0x01, max(0, min(100, int(pct))), 0xFF, 0x02, 0x01])


# ---- transport -------------------------------------------------------------------------------------------
async def _send(mac, kind, packets, timeout=12.0):
    if BleakClient is None:
        raise RuntimeError("bleak not installed (pip install bleak into the housedash venv)")
    char = LEDNET_WRITE if kind == "lednetwf" else ELK_WRITE
    last = None
    for attempt in range(3):
        try:
            async with BleakClient(mac, timeout=timeout) as c:
                if kind == "elk":
                    for p in ELK_LOGIN:
                        await c.write_gatt_char(char, p, response=False)
                        await asyncio.sleep(0.4)
                for p in packets:
                    if p is None:            # a pause (blink)
                        await asyncio.sleep(0.7); continue
                    await c.write_gatt_char(char, p, response=False)
                    await asyncio.sleep(0.15)
                return True
        except Exception as e:  # device busy / out of range / app holding the connection
            last = e
            await asyncio.sleep(1.0 + attempt)
    raise RuntimeError(f"{mac}: {last}")


async def power(mac, on, kind):
    return await _send(mac, kind, [lednet_power(on)] if kind == "lednetwf" else [elk_power(on)])


async def color(mac, r, g, b, bright=100, kind="lednetwf"):
    if kind == "lednetwf":
        return await _send(mac, kind, [lednet_power(True), lednet_color(r, g, b, bright)])
    pk = [elk_power(True), elk_color(r, g, b)]
    if bright != 100:
        pk.append(elk_bright(bright))
    return await _send(mac, kind, pk)


async def bright(mac, pct, kind):
    return await _send(mac, kind, [lednet_bright(pct)] if kind == "lednetwf" else [elk_bright(pct)])


async def blink(mac, kind):
    """Identify: red, blue, then warm white at full - so you can tell which strip this controller drives."""
    if kind == "lednetwf":
        pk = [lednet_power(True), lednet_color(255, 0, 0), None, lednet_color(0, 0, 255), None, lednet_color(255, 170, 90)]
    else:
        pk = [elk_power(True), elk_color(255, 0, 0), None, elk_color(0, 0, 255), None, elk_white(100)]
    return await _send(mac, kind, pk)


async def white(mac, pct, kind):
    if kind == "lednetwf":
        return await _send(mac, kind, [lednet_power(True), lednet_color_simple(0, 0, 0, int(pct * 2.55), int(pct * 2.55))])
    return await _send(mac, kind, [elk_power(True), elk_white(pct)])


async def scan(seconds=8.0):
    """Controllers in range: [{mac, name, kind, rssi}] - also saved to STATE for the dashboard."""
    if BleakScanner is None:
        raise RuntimeError("bleak not installed")
    found = {}
    def cb(d, adv):
        k = kind_of(d.name or adv.local_name)
        if k:
            found[d.address.upper()] = {"mac": d.address.upper(), "name": d.name or adv.local_name,
                                        "kind": k, "rssi": adv.rssi}
    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    now = int(time.time())
    for l in found.values():
        l["last"] = now
    try:                                   # keep controllers seen earlier (powered off / app connected right now)
        for l in json.load(open(STATE)).get("lights", []):
            if l["mac"].upper() not in found and l.get("last"):
                found[l["mac"].upper()] = dict(l, rssi=None)
    except Exception:
        pass
    out = sorted(found.values(), key=lambda x: x["name"] or "")
    try:
        json.dump({"t": now, "lights": out}, open(STATE, "w"))
    except OSError:
        pass
    return out


def _kind(argv, i, mac):
    if len(argv) > i and argv[i] in ("lednetwf", "elk"):
        return argv[i]
    try:
        for l in json.load(open(STATE))["lights"]:
            if l["mac"].upper() == mac.upper():
                return l["kind"]
    except Exception:
        pass
    return "lednetwf"


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd = a[0]
    if cmd == "scan":
        print(json.dumps(asyncio.run(scan(float(a[1]) if len(a) > 1 else 8.0)), indent=1)); return 0
    mac = a[1].upper()
    if cmd in ("on", "off"):
        asyncio.run(power(mac, cmd == "on", _kind(a, 2, mac)))
    elif cmd == "color":
        r, g, b = (int(x) for x in a[2:5]); br = int(a[5]) if len(a) > 5 and a[5].isdigit() else 100
        asyncio.run(color(mac, r, g, b, br, _kind(a, 6 if len(a) > 5 and a[5].isdigit() else 5, mac)))
    elif cmd == "bright":
        asyncio.run(bright(mac, int(a[2]), _kind(a, 3, mac)))
    elif cmd == "blink":
        asyncio.run(blink(mac, _kind(a, 2, mac)))
    elif cmd == "white":
        pct = int(a[2]) if len(a) > 2 and a[2].isdigit() else 100
        asyncio.run(white(mac, pct, _kind(a, 3 if len(a) > 2 and a[2].isdigit() else 2, mac)))
    else:
        print(__doc__); return 2
    print("ok"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
