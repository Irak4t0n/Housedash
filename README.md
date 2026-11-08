# housedash — a house dashboard for your TVs, served from a Raspberry Pi

One dark, TV-sized page with everything about the house on it, a news/UFC/esports/hacker wire scrolling along the bottom, and a Fire TV screensaver app that shows it whenever the TV is idle — with the remote's ◀ ▶ OK buttons driving the wire so you can actually open a headline (QR to your phone, or the TV's browser).

![the dashboard](docs/dashboard.png)

Everything is configured from a settings page on the Pi (no files to edit), and the whole thing is standard-library Python + one static HTML file, so it runs happily on a Pi Zero 2 W next to Pi-hole.

## What's on it

| Card | Source | Notes |
|---|---|---|
| **Weather** | api.weather.gov (NWS, no key) | hourly strip, 3-day, active alerts, and a **drone-flying verdict** (good / ok / windy) from the next 6 h of wind |
| **Tide** | NOAA CO-OPS (no key) | next 4 highs/lows for your station, water temperature |
| **What's biting** | built-in month table + rules | Alabama Gulf Coast by default; tips from water temp, wind and the next tide. Edit `BITING` in `outdoors.py` for your coast |
| **Devices** | Pi-hole's network table | online / last seen for the devices you name |
| **Pi-hole** | Pi-hole v6 API | blocked %, totals, top blocked, a Pac-Man "ghosts eaten" counter |
| **Printers** | Anycubic LAN mode (MQTT) | state, nozzle/bed/chamber, file, layer, progress, time left; lifetime / this-week / hours counters |
| **VPN** | `wg show` | WireGuard peers, green when they've handshaken recently |
| **Lights** | WiZ UDP | on/off + brightness per bulb |
| **Internet · Pi** | ping, ipify, DNS | latency/loss every 10 s, public IP vs your DDNS name, Pi temp/load/mem/uptime |
| **Websites** | HTTPS | up/down, response time, **TLS days left**, a **defacement fingerprint** of the home page; WordPress sites can add pending comments + Fluent Forms lead counts |
| **Calendar** | any iCal/ICS URL | today + tomorrow from Google / iCloud / Outlook secret iCal links — no OAuth, no Google Cloud project |
| **The wire** | RSS + ESPN | UFC results & next card, hacker/infosec, esports, gaming, Hacker News, drones — every feed configurable, add your own |

Plus **alerts**: a new MAC on the network puts a banner on every TV and plays a sound (bring your own clip); a known device coming back after hours away gets a quieter chime, rate-limited and silent during quiet hours. The same alerts can run on a Windows PC as a small system-tray app.

![TV remote: QR popup](docs/tv-qr.png)

## Install (Pi)

Any Raspberry Pi running Raspberry Pi OS / Debian 12+. Pi-hole v6 is optional (the Pi-hole and Devices cards need it); WireGuard, WiZ bulbs and Anycubic printers are optional too — cards with nothing behind them just say so, or switch them off.

```bash
git clone https://github.com/Irak4t0n/housedash
sudo housedash/pi/install.sh
```

The installer pulls the few packages it needs, creates a venv for the printer and calendar libraries, asks for a Pi-hole app password (Pi-hole → Settings → Web interface/API → *Configure app password*), installs eight small systemd services and prints two addresses:

- **`http://<pi>:8088`** — the dashboard (what the TVs show)
- **`http://<pi>:8089`** — the settings page

Open the settings page and fill in what you have: house name, lat/lon (the NWS grid is resolved automatically), NOAA tide stations, printers / bulbs / devices / VPN peers by IP, websites, feeds. Changes reach the TVs within ~10 s. The two secrets — calendar iCal links and a WordPress application password — are entered on the same page and stored root-only; they're never displayed back.

Re-run `install.sh` to update after a `git pull`.

> The settings page has no login: it is meant for a home LAN (and your own VPN). Don't port-forward 8089.

### Sounds
`pi/web/sounds/` ships a generated chime (`returned.mp3`). Drop your own `newdevice.mp3` (and optionally `.ogg`) next to it for the new-device alert — the author uses a certain famous battle cry, which is not something to redistribute.

## The TV app (Fire TV / Android TV)

`tv-app/` is a 17 KB APK: one WebView, a **DreamService** (screensaver) and a launcher activity (for a Projectivy tile or "Your Apps").

- Screensaver stays up while you use **◀ ▶** to step through the wire; **OK** shows a QR code to scan with your phone; OK again opens the link in the TV browser; **Back** closes the popup, or wakes the TV. Any other button wakes the TV as usual.
- Retries every 15 s if the Pi is unreachable. **MENU** in the app opens a one-field URL setting.

Build (no Gradle; needs a JDK and Android build-tools 35 + platform 34):

```bash
cd tv-app
keytool -genkeypair -v -keystore housedash.keystore -alias housedash -keyalg RSA -keysize 2048 \
        -validity 10000 -storepass housedash -keypass housedash -dname "CN=housedash"   # once; keep it
DASH_URL=http://192.168.1.50:8088/ ./build.sh
adb connect <tv-ip>:5555 && adb install -r build/HouseDashboard.apk
adb shell settings put secure screensaver_components com.irak4t0n.housedash/.DashDream   # make it the screensaver
```

Works on Fire OS 7 (32-bit sticks) and Fire OS 8 TVs. Fire TV's own idle timeout (default 5 min) decides when it comes on.

## PC tray alert (Windows)

`pc/newdevice-alert.ps1` polls the dashboard and plays the sounds / shows a toast on your desktop. It's a tray icon (right-click: status, mute 1 h, test, open dashboard, quit), launched at logon from your Startup folder with no console window. Set `$Pi` at the top, put your sounds in `pc/sounds/`, double-click `Install HouseDash alert.cmd`. `-Uninstall` removes it.

## How it's put together

```
pi/collect.py    every 10 s   Pi-hole API, wg show, device presence + new/returning logic, WiZ, Pi stats
pi/outdoors.py   10 s / 10 m  internet health / NWS + NOAA + fishing
pi/sites.py      every 20 s   websites
pi/ticker.py     every 5 m    the wire (RSS + ESPN scoreboard)
pi/gcal.py       every 5 m    iCal feeds
pi/printers.py   live         Anycubic LAN-mode MQTT (venv: anycubic-lan)
pi/setup.py      :8089        settings page -> /etc/housedash/config.json
pi/hdconfig.py                defaults + deep-merge; every service re-reads config each loop
pi/web/index.html             the page; polls state.json, auto-scrolls overflowing cards, runs the wire and TV remote mode
```

Each collector writes a small JSON file under `/var/lib/housedash/`; `collect.py` folds them (and the config) into `state.json`, which `python3 -m http.server` serves on 8088. No database, no framework, no build step. `qrcode.min.js` ([qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator), MIT) is the only JavaScript dependency and is downloaded by the installer.

Secrets never go in `config.json`: Pi-hole password, calendar links and WordPress credentials live in `/etc/housedash/` as root-only files.

## License

MIT. Built by [Irak4t0n](https://github.com/Irak4t0n) for his own living room; PRs with new cards and feeds welcome.
