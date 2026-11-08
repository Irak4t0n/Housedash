#!/bin/bash
# housedash installer / updater for a Raspberry Pi (Debian/Raspberry Pi OS). Re-run it any time to update.
#   git clone https://github.com/Irak4t0n/housedash && sudo housedash/pi/install.sh
set -e
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
cd "$(dirname "$0")"
echo "== packages"
apt-get install -y -qq python3 python3-venv python3-qrcode jq wireguard-tools iputils-ping >/dev/null
id housedash >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin housedash
echo "== files"
mkdir -p /opt/housedash /var/lib/housedash/sounds /etc/housedash
install -m755 collect.py outdoors.py sites.py ticker.py gcal.py printers.py setup.py /opt/housedash/
install -m644 hdconfig.py /opt/housedash/
install -m755 housedash-add /usr/local/sbin/housedash-add
install -m644 web/index.html /var/lib/housedash/
install -m644 web/sounds/* /var/lib/housedash/sounds/
if [ ! -f /var/lib/housedash/qrcode.min.js ]; then
  curl -fsSL -o /var/lib/housedash/qrcode.min.js https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js || echo "  (qrcode.min.js not downloaded - TV QR popups need it; fetch it later)"
fi
[ -f /var/lib/housedash/sounds/newdevice.mp3 ] || cp /var/lib/housedash/sounds/returned.mp3 /var/lib/housedash/sounds/newdevice.mp3   # placeholder until you drop in your own
chown -R housedash:housedash /var/lib/housedash
chmod 755 /etc/housedash
echo "== python venv (anycubic-lan for printers, icalendar for calendars)"
[ -d /opt/housedash/venv ] || python3 -m venv /opt/housedash/venv
/opt/housedash/venv/bin/pip install -q --upgrade anycubic-lan icalendar recurring_ical_events
echo "== Pi-hole"
if [ ! -s /etc/housedash/pihole_password ]; then
  echo "The dashboard reads Pi-hole v6 stats through its API. Create an app password in Pi-hole:"
  echo "  Settings > Web interface / API > 'Configure app password' - paste it here (or leave blank to skip the Pi-hole card)."
  read -r -p "Pi-hole app password: " PW </dev/tty || PW=""
  [ -n "$PW" ] && { printf '%s\n' "$PW" > /etc/housedash/pihole_password; chmod 600 /etc/housedash/pihole_password; }
fi
IP=$(hostname -I | awk '{print $1}')
if [ ! -f /etc/housedash/config.json ]; then
  printf '{"network": {"pi_ip": "%s"}}\n' "$IP" > /etc/housedash/config.json
fi
echo "== services"
install -m644 systemd/*.service /etc/systemd/system/
systemctl daemon-reload
for s in collect outdoors sites ticker calendar printers web setup; do systemctl enable --now housedash-$s >/dev/null 2>&1 || true; systemctl restart housedash-$s; done
sleep 3
echo
echo "Dashboard:  http://$IP:8088      Settings: http://$IP:8089"
systemctl --no-pager --no-legend list-units 'housedash*' | awk '{print "  " $1, $3, $4}'
