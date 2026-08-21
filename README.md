# servarr
*arr stack

## Two environments

This repo is deployed by Komodo in two configurations. Both point to the same compose file and use Komodo
variables to deploy different stacks.

| Branch | Stack in Komodo | Compose files | Port on tailnet | Container name |
|--------|-----------------|---------------|-----------------|----------------|
| `main` | `jellyfin`      | `compose.yaml` | `:8096`         | `jellyfin`     |
| `devel`  | `jellyfin-dev`  | `compose.yaml` | `:8097` | `jellyfin-dev` |

Komodo reads the stack definitions from [`broomej/komodo-config`](https://github.com/broomej/komodo-config) via Resource Sync. Each stack points back at this repo for its compose files.

The most important property of the dev environment is the **read-only media mount**. Dev jellyfin can read your media library to verify playback, but it cannot write metadata, NFO files, or images back into the prod media tree. So a breaking change to metadata handling in a new image version cannot corrupt your real library. Dev gluetun similarly gets its own config directory so a botched tunnel setup can't overwrite prod's cached WireGuard state.

## Environment variables

Required for any stack bring-up. Set these in Komodo (per-stack env vars) or a local `.env` file:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TAILNET_IP` | yes | — | Tailnet IP of `daggoth` (e.g. `100.x.y.z`). Use `127.0.0.1` for local-only testing. |
| `DOCKER_VOLUMES` | yes | — | Host path under which per-service config dirs live (e.g. `/srv/docker-volumes`). |
| `SERVARR_DATA` | yes | — | Host path to the media library root (mounted as `/data` inside jellyfin). |
| `TZ` | no | `America/Los_Angeles` | Pass through to all containers via `TZ`. |
| `PROTONVPN_WG_PRIVATE_KEY` | yes (for gluetun) | — | WireGuard private key from Proton's WireGuard config file (`PrivateKey = ...` line). **Never commit this** — use Komodo secrets or a gitignored `.env`. |
| `VPN_SERVICE_PROVIDER` | no | `protonvpn` | Gluetun provider name. Override only if switching providers. |
| `PROTONVPN_SERVER_COUNTRY` | no | `Netherlands` | P2P-friendly country for gluetun to connect through. Pick a country Proton tags with the double-arrow (P2P) icon. |
| `LAN_SUBNET` | no | `100.64.0.0/10` | CIDR that gluetun-networked containers are allowed to reach (LAN / tailnet side). Set to your real LAN CIDR (e.g. `192.168.1.0/24`) if you're not on tailnet. |
| `PROTONVPN_PORT_FORWARDING` | no | `off` | Set to `on` once you've added qbittorrent AND confirmed your ProtonVPN plan supports port forwarding (Plus / Unlimited). Leaving it off lets gluetun start cleanly on any plan. |

## Verifying gluetun before wiring up the rest of the stack

Gluetun is the foundation every VPN-routed container depends on. Verify it on its own before adding qbittorrent / prowlarr / radarr / sonarr:

```bash
# 1. Bring up just gluetun (dev stack, after merging to dev branch):
#    Komodo deploys it; or locally:
docker compose -p servarr-dev -f compose.yaml -f compose.dev.yaml up -d gluetun

# 2. Confirm the tunnel is up — IP should be a ProtonVPN exit, NOT your home IP:
docker exec gluetun-dev wget -qO- https://am.i.mullvad.net/json | jq '{ip, country, mullvad_exit_ip}'

# 3. Test the kill switch — stopping gluetun should kill network for any
#    container using `network_mode: service:gluetun`. (No such container exists
#    yet — that's the point of doing this verification first.)
docker stop gluetun-dev

# 4. Restart and confirm gluetun recovers automatically:
docker start gluetun-dev
docker exec gluetun-dev wget -qO- http://localhost:8000/v1/healthcheck
#  → 204 No Content = healthy
```

Only once that's solid should you move on to adding qbittorrent (which will declare `network_mode: service:gluetun` and publish its web UI on a port in gluetun's `ports:` list).

## Workflow

1. Cut a feature branch off `dev`:
   ```bash
   git checkout dev && git pull
   git checkout -b feature/test-new-jellyfin-image
   ```
2. Make your changes to `compose.yaml` (if you want them to ship to prod) and/or `compose.dev.yaml` (if they're dev-only for now).
3. Push and open a PR against `dev`. CI runs `docker compose -f compose.yaml config` and `docker compose -f compose.yaml -f compose.dev.yaml config` to catch YAML / env-var errors.
4. Merge into `dev`. Komodo auto-redeploys the `jellyfin-dev` stack within ~30s (via push webhook).
5. Manually verify at `http://<tailnet>:8097`.
6. Open a PR `dev → main`. Branch protection on `main` requires PR review (you can self-review as a solo dev).
7. Merge. Komodo redeploys `jellyfin` (prod).

## CI

`.github/workflows/validate-compose.yml` runs on every PR. It runs `docker compose config` for both prod and dev compose combinations to catch:
- YAML syntax errors
- Unresolved `${VARIABLE}` references (would fail at deploy time)
- Invalid `deploy.resources` shape (Komodo passes these to Docker verbatim)

## Adding a new app to the stack

When the *arr stack grows (sonarr, radarr, etc.), add the service to `compose.yaml` (prod baseline) AND decide how dev should differ. Add an entry under `services:` in `compose.dev.yaml` mirroring the existing pattern: separate container name, separate port, separate config volume, and (for media-consuming apps) read-only media mount in dev.

Then add two new TOML files in `broomej/komodo-config/stacks/`: `<app>.toml` (prod) and `<app>-dev.toml` (dev), following the existing `jellyfin.toml` / `jellyfin-dev.toml` pair.

### Two patterns depending on whether the app needs VPN egress

**Pattern A — direct network (jellyfin, radarr, sonarr, overseerr, prowlarr-on-LAN):**
Just declare `ports:` on the service. The app talks to the host network directly. Most *arr apps and Jellyfin itself fit here — they don't touch torrents, so they don't need gluetun.

**Pattern B — VPN-routed (qbittorrent, any torrent-indexer that hits public trackers):**
Declare `network_mode: service:gluetun` on the service and do NOT declare a `ports:` block on the service itself — instead, publish the app's web UI port in the **gluetun** service's `ports:` list, because the dependent container has no network namespace of its own to publish on. Example (sketch, not yet in this repo):

```yaml
# In compose.yaml, added to the gluetun service's ports:
#   - "${TAILNET_IP}:8080:8080"   # qbittorrent web UI (via gluetun's netns)

# And the qbittorrent service itself:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    network_mode: service:gluetun   # ← inherits gluetun's netns, including VPN egress
    # NOTE: no `ports:` block here — those go on gluetun.
    environment:
      WEBUI_PORT: 8080
    volumes:
      - ${DOCKER_VOLUMES}/qbittorrent/config:/config
      - ${SERVARR_DATA}/downloads:/downloads   # same /data root as jellyfin sees
    depends_on:
      gluetun:
        condition: service_healthy
```

The `depends_on: gluetun (service_healthy)` line is important — without it, qbittorrent may start before the tunnel is up and leak on its initial DNS resolution. The gluetun healthcheck in `compose.yaml` makes that condition meaningful.

## Local testing without Komodo

You can run the dev stack locally (e.g. on a laptop) without Komodo to iterate on compose changes:

```bash
cp .env.example .env   # then edit values
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -p jellyfin-dev -f compose.yaml -f compose.dev.yaml up -d
```

Note: this requires the same env vars (`TAILNET_IP`, `DOCKER_VOLUMES`, `SERVARR_DATA`, `TZ`, and for gluetun `PROTONVPN_WG_PRIVATE_KEY`) to be set. Use `127.0.0.1` for `TAILNET_IP` if you're not on a tailnet. See the **Environment variables** table above for the full list.
