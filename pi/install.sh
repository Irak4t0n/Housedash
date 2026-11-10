#!/bin/bash
# housedash installer / updater for a Raspberry Pi (Debian/Raspberry Pi OS). Re-run it any time to update.
#   git clone https://github.com/Irak4t0n/housedash && sudo housedash/pi/install.sh
set -e
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
cd "$(dirname "$0")"
echo "== packages"
apt-get install -y -qq python3 python3-venv python3-qrcode python3-pil fonts-dejavu-core jq wireguard-tools iputils-ping nmap speedtest-cli dnsutils >/dev/null
id housedash >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin housedash
echo "== files"
mkdir -p /opt/housedash /var/lib/housedash/sounds /etc/housedash
install -m755 collect.py outdoors.py sites.py ticker.py gcal.py printers.py setup.py speedtest.py canary.py audit.py blelights.py render.py /opt/housedash/
install -m644 hdconfig.py notify.py wizctl.py flight.py /opt/housedash/
install -m755 extras/unbound-setup.sh /usr/local/sbin/unbound-setup.sh
install -m755 housedash-add /usr/local/sbin/housedash-add
install -m644 web/index.html web/santa.js /var/lib/housedash/
install -m644 web/sounds/* /var/lib/housedash/sounds/
if [ ! -f /var/lib/housedash/qrcode.min.js ]; then
  curl -fsSL -o /var/lib/housedash/qrcode.min.js https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js || echo "  (qrcode.min.js not downloaded - TV QR popups need it; fetch it later)"
fi
[ -f /var/lib/housedash/sounds/newdevice.mp3 ] || cp /var/lib/housedash/sounds/returned.mp3 /var/lib/housedash/sounds/newdevice.mp3   # placeholder until you drop in your own
chown -R housedash:housedash /var/lib/housedash
chmod 755 /etc/housedash
echo "== python venv (anycubic-lan for printers, icalendar for calendars, bleak for Bluetooth lights)"
[ -d /opt/housedash/venv ] || python3 -m venv /opt/housedash/venv
/opt/housedash/venv/bin/pip install -q --upgrade anycubic-lan icalendar recurring_ical_events bleak
# Bluetooth lights (Zengge / Magic Lantern): keep the radio powered after boot
if [ -f /etc/bluetooth/main.conf ]; then grep -q "^AutoEnable=true" /etc/bluetooth/main.conf || { sed -i "s/^#*AutoEnable=.*/AutoEnable=true/" /etc/bluetooth/main.conf; grep -q "^AutoEnable" /etc/bluetooth/main.conf || echo "AutoEnable=true" >> /etc/bluetooth/main.conf; }; rfkill unblock bluetooth 2>/dev/null || true; fi
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
install -m644 systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
for s in collect outdoors sites ticker calendar printers web setup canary render; do systemctl enable --now housedash-$s >/dev/null 2>&1 || true; systemctl restart housedash-$s; done
systemctl enable --now housedash-speedtest.timer housedash-audit.timer housedash-blescan.timer >/dev/null 2>&1 || true
sleep 3
echo
echo "Dashboard:  http://$IP:8088      Settings: http://$IP:8089   (phone notifications: Settings > notify)"
echo "Optional recursive DNS: sudo unbound-setup.sh"
systemctl --no-pager --no-legend list-units 'housedash*' | awk '{print "  " $1, $3, $4}'
