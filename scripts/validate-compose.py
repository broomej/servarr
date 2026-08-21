#!/usr/bin/env python3
"""Validate compose files in the servarr repo.

Runs `docker compose config` for both the prod baseline and the merged dev
override, then asserts the dev override actually does what it claims to:
  - jellyfin: container_name overridden to `jellyfin-dev`, port to 8097→8096,
    config volume points to jellyfin-dev/config, media mount read-only,
    NVIDIA GPU still attached.
  - gluetun: container_name overridden to `gluetun-dev`, control port to
    8001→8000, config volume points to gluetun-dev/config.

Handles BOTH short-form and long-form output from `docker compose config`,
because docker compose normalizes short-form syntax (e.g. `IP:8097:8096`)
into long-form dicts (e.g. `{host_ip: ..., published: "8097", target: 8096}`).

Run locally:
    python scripts/validate-compose.py

CI:
    python scripts/validate-compose.py   # exit 0 = all OK, exit 1 = failure
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Stand-in env vars so `docker compose config` can resolve ${VAR} references
# even on a CI runner that doesn't have the real homelab paths or ProtonVPN
# credentials. PROTONVPN_WG_PRIVATE_KEY is required by compose.yaml (via the
# `${VAR:?error}` syntax) so we set a dummy value here — it never gets used
# because `docker compose config` only renders the config, doesn't start the
# container.
ENV = {
    "TAILNET_IP": "100.64.0.1",
    "TZ": "America/Los_Angeles",
    "DOCKER_VOLUMES": "/tmp/docker",
    "SERVARR_DATA": "/tmp/media",
    "PROTONVPN_WG_PRIVATE_KEY": "ci-dummy-key-not-real",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}


def run_compose_config(*file_paths: str) -> dict:
    """Run `docker compose -f ... config --format json` and return parsed dict."""
    cmd = ["docker", "compose"]
    for f in file_paths:
        cmd.extend(["-f", f])
    cmd.extend(["config", "--format", "json"])
    result = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
    if result.returncode != 0:
        print("  FAIL  docker compose config failed:")
        print("        stderr: " + result.stderr.strip().replace("\n", "\n        "))
        sys.exit(2)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  FAIL  could not parse compose config JSON: {e}")
        sys.exit(2)


def assert_check(name: str, condition: bool, detail: str = "") -> bool:
    sym = "OK  " if condition else "FAIL"
    msg = f"  {sym}  {name}"
    if not condition and detail:
        msg += f"\n        {detail}"
    print(msg)
    return condition


def check_port(ports, published, target) -> bool:
    """Match a port in either short-form string or long-form dict."""
    for p in ports or []:
        if isinstance(p, str):
            # Short form: "100.64.0.1:8097:8096" / "8097:8096" / "8097"
            parts = p.split(":")
            if str(published) in parts and str(target) in parts:
                return True
        elif isinstance(p, dict):
            if str(p.get("published")) == str(published) and p.get("target") == target:
                return True
    return False


def check_volume(volumes, target: str, *, read_only=None, source_contains=None) -> bool:
    """Match a volume mount on target path (and optionally read_only flag / source substring).

    Handles both short-form (string) and long-form (dict) representations.
    """
    for v in volumes or []:
        if isinstance(v, str):
            # Short form: "/host:/container:ro" or "/host:/container"
            parts = v.split(":")
            if len(parts) < 2:
                continue
            if parts[1] != target:
                continue
            if read_only is not None:
                ro_flags = parts[2] if len(parts) >= 3 else ""
                if ("ro" in ro_flags) != read_only:
                    continue
            if source_contains is not None and source_contains not in parts[0]:
                continue
            return True
        elif isinstance(v, dict):
            if v.get("target") != target:
                continue
            if read_only is not None and v.get("read_only") != read_only:
                continue
            if source_contains is not None and source_contains not in str(v.get("source", "")):
                continue
            return True
    return False


def has_nvidia_gpu(svc_config) -> bool:
    """Check for NVIDIA GPU passthrough in deploy.resources.reservations.devices."""
    devices = (
        svc_config.get("deploy", {})
        .get("resources", {})
        .get("reservations", {})
        .get("devices", [])
    ) or []
    for d in devices:
        if d.get("driver") == "nvidia":
            return True
        caps = d.get("capabilities") or []
        if isinstance(caps, list) and "gpu" in caps:
            return True
    return False


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    print("=== Validating prod compose (compose.yaml only) ===")
    try:
        prod = run_compose_config(str(repo_root / "compose.yaml"))
    except FileNotFoundError:
        print("  FAIL  compose.yaml not found at repo root")
        return 1
    if "services" not in prod or not prod["services"]:
        print("  FAIL  compose.yaml has no services")
        return 1
    print(f"  OK    prod compose parses, {len(prod['services'])} service(s): {list(prod['services'])}")

    print("\n=== Validating dev compose (compose.yaml + compose.dev.yaml) ===")
    try:
        dev = run_compose_config(
            str(repo_root / "compose.yaml"),
            str(repo_root / "compose.dev.yaml"),
        )
    except FileNotFoundError as e:
        print(f"  FAIL  {e}")
        return 1

    jelly = dev.get("services", {}).get("jellyfin", {})
    if not jelly:
        print("  FAIL  no 'jellyfin' service in merged dev compose")
        return 1

    all_ok = True
    all_ok &= assert_check(
        "container_name overridden to jellyfin-dev",
        jelly.get("container_name") == "jellyfin-dev",
        f"got: {jelly.get('container_name')!r}",
    )
    all_ok &= assert_check(
        "port overridden to published=8097, target=8096",
        check_port(jelly.get("ports"), 8097, 8096),
        f"got: {jelly.get('ports')!r}",
    )
    all_ok &= assert_check(
        "config volume overridden to jellyfin-dev/config",
        check_volume(jelly.get("volumes"), "/config", source_contains="jellyfin-dev"),
        f"got: {jelly.get('volumes')!r}",
    )
    all_ok &= assert_check(
        "media mounted read-only at /data",
        check_volume(jelly.get("volumes"), "/data", read_only=True),
        f"got: {jelly.get('volumes')!r}",
    )
    all_ok &= assert_check(
        "NVIDIA GPU still attached",
        has_nvidia_gpu(jelly),
        "deploy.resources.reservations.devices has no nvidia/gpu entry",
    )

    # ── Gluetun dev override checks ──────────────────────────────────────
    # Same pattern as jellyfin: prod binds :8000, dev binds :8001 on the same
    # tailnet IP, and dev gets its own config dir so tunnel state is isolated.
    print("\n=== Gluetun dev override checks ===")
    glue = dev.get("services", {}).get("gluetun", {})
    if not glue:
        print("  FAIL  no 'gluetun' service in merged dev compose")
        return 1

    all_ok &= assert_check(
        "gluetun container_name overridden to gluetun-dev",
        glue.get("container_name") == "gluetun-dev",
        f"got: {glue.get('container_name')!r}",
    )
    all_ok &= assert_check(
        "gluetun control port overridden to published=8001, target=8000",
        check_port(glue.get("ports"), 8001, 8000),
        f"got: {glue.get('ports')!r}",
    )
    all_ok &= assert_check(
        "gluetun config volume overridden to gluetun-dev/config",
        check_volume(glue.get("volumes"), "/gluetun", source_contains="gluetun-dev"),
        f"got: {glue.get('volumes')!r}",
    )
    # Sanity: dev gluetun should NOT mount /data — it's a network appliance,
    # not a media consumer. If someone adds the mount by mistake, catch it.
    all_ok &= assert_check(
        "gluetun does NOT mount /data (network appliance, no media access)",
        not check_volume(glue.get("volumes"), "/data"),
        "gluetun should not see the media library — check compose.dev.yaml",
    )

    print()
    if all_ok:
        print("All dev override checks passed.")
        return 0
    print("Some dev override checks failed — see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
