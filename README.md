# servarr
*arr stack

## Two environments

This repo is deployed by Komodo in two configurations:

| Branch | Stack in Komodo | Compose files | Port on tailnet | Container name |
|--------|-----------------|---------------|-----------------|----------------|
| `main` | `jellyfin`      | `compose.yaml` | `:8096`         | `jellyfin`     |
| `dev`  | `jellyfin-dev`  | `compose.yaml` + `compose.dev.yaml` | `:8097` | `jellyfin-dev` |

Komodo reads the stack definitions from [`broomej/komodo-config`](https://github.com/broomej/komodo-config) via Resource Sync. Each stack points back at this repo for its compose files.

## What `compose.dev.yaml` changes

It is applied as a Docker Compose **override** on top of `compose.yaml`. Override semantics:

| Field | Prod (`compose.yaml`) | Dev (`compose.dev.yaml` override) |
|---|---|---|
| `container_name` | `jellyfin` | `jellyfin-dev` |
| `ports` | `:8096:8096` | `:8097:8096` |
| `/config` volume | `${DOCKER_VOLUMES}/jellyfin/config` | `${DOCKER_VOLUMES}/jellyfin-dev/config` |
| `/cache` volume  | `jellyfin-cache` (named) | `jellyfin-dev-cache` (named) |
| `/data` volume   | `${SERVARR_DATA}` (rw) | `${SERVARR_DATA}` **read-only** |
| GPU passthrough  | yes | yes (kept — dev exists partly to test NVIDIA changes) |

The most important property of the dev environment is the **read-only media mount**. Dev jellyfin can read your media library to verify playback, but it cannot write metadata, NFO files, or images back into the prod media tree. So a breaking change to metadata handling in a new image version cannot corrupt your real library.

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

When the *arr stack grows (sonarr, radarr, etc.), add the service to `compose.yaml` (prod baseline) AND decide how dev should differ. Add an entry under `services:` in `compose.dev.yaml` mirroring the jellyfin pattern: separate container name, separate port, separate config volume, read-only media mount.

Then add two new TOML files in `broomej/komodo-config/stacks/`: `<app>.toml` (prod) and `<app>-dev.toml` (dev), following the existing `jellyfin.toml` / `jellyfin-dev.toml` pair.

## Local testing without Komodo

You can run the dev stack locally (e.g. on a laptop) without Komodo to iterate on compose changes:

```bash
cp .env.example .env   # then edit values
docker compose -f compose.yaml -f compose.dev.yaml config
docker compose -p jellyfin-dev -f compose.yaml -f compose.dev.yaml up -d
```

Note: this requires the same env vars (`TAILNET_IP`, `DOCKER_VOLUMES`, `SERVARR_DATA`, `TZ`) to be set. Use `127.0.0.1` for `TAILNET_IP` if you're not on a tailnet.
# smoke test Wed Aug 19 09:01:57 PM PDT 2026
