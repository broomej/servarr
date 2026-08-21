#!/command:with-contenv bash
# Idempotent: ensure WebUI\LocalHostAuth=false so the gluetun sidecar
# can call /api/v2/* from 127.0.0.1 without a session cookie.
CONF="/config/qBittorrent/qBittorrent.conf"
mkdir -p "$(dirname "$CONF")"
[ -f "$CONF" ] || echo "[Preferences]" > "$CONF"

if grep -q '^WebUI\\LocalHostAuth=' "$CONF"; then
  sed -i 's/^WebUI\\LocalHostAuth=.*/WebUI\\LocalHostAuth=false/' "$CONF"
else
  printf 'WebUI\\LocalHostAuth=false\n' >> "$CONF"
fi
