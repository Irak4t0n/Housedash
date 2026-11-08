#!/bin/bash
# Optional: make Pi-hole fully recursive with Unbound (no upstream resolver sees your queries).
# Tested on Raspberry Pi OS / Debian 12-13 with Pi-hole v6. Re-run safe. Undo: unbound-setup.sh --undo
set -e
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
if [ "$1" = "--undo" ]; then
  pihole-FTL --config dns.upstreams '["9.9.9.9","149.112.112.112"]' >/dev/null
  systemctl disable --now unbound >/dev/null 2>&1 || true
  echo "Pi-hole upstream back to Quad9; unbound stopped."; exit 0
fi
PREV=$(pihole-FTL --config dns.upstreams 2>/dev/null | tr -d '\n' || echo "[]")
echo "current Pi-hole upstreams: $PREV"
apt-get install -y -qq unbound dns-root-data dnsutils >/dev/null
systemctl disable --now unbound-resolvconf.service >/dev/null 2>&1 || true
cat > /etc/unbound/unbound.conf.d/pi-hole.conf <<'EOF'
server:
    verbosity: 0
    interface: 127.0.0.1
    port: 5335
    do-ip4: yes
    do-udp: yes
    do-tcp: yes
    do-ip6: no
    prefer-ip6: no
    # root hints come from the dns-root-data package (kept current by apt)
    root-hints: "/usr/share/dns/root.hints"
    harden-glue: yes
    harden-dnssec-stripped: yes
    use-caps-for-id: no
    edns-buffer-size: 1232
    prefetch: yes
    num-threads: 1
    so-rcvbuf: 1m
    private-address: 192.168.0.0/16
    private-address: 169.254.0.0/16
    private-address: 172.16.0.0/12
    private-address: 10.0.0.0/8
    private-address: fd00::/8
    private-address: fe80::/10
    # small-memory tuning for a Pi Zero
    msg-cache-size: 8m
    rrset-cache-size: 16m
    cache-min-ttl: 60
    serve-expired: yes
EOF
systemctl enable --now unbound
systemctl restart unbound
sleep 2
A=$(dig @127.0.0.1 -p 5335 +short +time=5 pi-hole.net | head -1)
[ -n "$A" ] || { echo "unbound did not answer - leaving Pi-hole upstreams unchanged"; exit 1; }
echo "test: pi-hole.net -> $A"
BAD=$(dig @127.0.0.1 -p 5335 +short +time=5 fail01.dnssec.works || true)
[ -z "$BAD" ] && echo "DNSSEC validation: ok (bad signature rejected)"
pihole-FTL --config dns.upstreams '["127.0.0.1#5335"]' >/dev/null
echo "Pi-hole now resolves through unbound (127.0.0.1#5335). collect.py's DNS guard reverts to Quad9 and notifies if unbound stops answering."
echo "unbound memory: $(ps -o rss= -C unbound | awk '{s+=$1} END {print int(s/1024) " MB"}')"
