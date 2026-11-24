#!/usr/bin/env python3
"""Preflight environment & multi-profile isolation check for agy-create-colab-notebook."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def get_homes() -> tuple[Path, Path, bool]:
    """Return (env_home, os_home, is_isolated)."""
    env_home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    try:
        os_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        os_home = env_home
    return env_home, os_home, env_home.resolve() != os_home.resolve()


def build_search_env(env_home: Path, os_home: Path) -> dict[str, str]:
    """Augment PATH with common macOS/Linux user tool directories."""
    env = dict(os.environ)
    extras = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(env_home / ".local" / "bin"),
        str(os_home / ".local" / "bin"),
    ]
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for item in extras:
        if item not in parts and Path(item).is_dir():
            parts.append(item)
    env["PATH"] = os.pathsep.join(parts)
    return env


def discover_rclone_configs(
    explicit: str | None, env_home: Path, os_home: Path
) -> tuple[str | None, list[str]]:
    """Locate active rclone config and candidate files without reading secrets."""
    candidates: list[Path] = []
    for key in (explicit, os.environ.get("COLAB_RCLONE_CONFIG"), os.environ.get("RCLONE_CONFIG")):
        if key:
            p = Path(key).expanduser()
            if p.is_file() and p not in candidates:
                candidates.append(p)
    for base in (env_home, os_home):
        rc_dir = base / ".config" / "rclone"
        if rc_dir.is_dir():
            for conf in sorted(rc_dir.glob("*.conf")):
                if conf.is_file() and conf not in candidates:
                    candidates.append(conf)
    active = str(candidates[0]) if candidates else None
    return active, [str(p) for p in candidates]


def discover_colab_config(explicit: str | None, env_home: Path, os_home: Path) -> dict[str, Any]:
    """Locate colab-cli sessions.json path across env and OS homes."""
    override = explicit or os.environ.get("COLAB_CLI_CONFIG")
    env_path = env_home / ".config" / "colab-cli" / "sessions.json"
    os_path = os_home / ".config" / "colab-cli" / "sessions.json"
    active = Path(override).expanduser() if override else (env_path if env_path.exists() else os_path)
    return {
        "active": str(active),
        "exists": active.exists(),
        "env_home_exists": env_path.exists(),
        "os_home_exists": os_path.exists(),
    }


def run_cmd(cmd: list[str], env: dict[str, str], timeout: int = 15) -> tuple[int, str, str]:
    """Execute a short diagnostic command safely."""
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def list_rclone_remotes(rclone_bin: str | None, config_path: str | None, env: dict[str, str]) -> list[str]:
    """List remote names only (never prints credentials)."""
    if not rclone_bin:
        return []
    cmd = [rclone_bin, "listremotes"]
    if config_path:
        cmd.extend(["--config", config_path])
    rc, out, _ = run_cmd(cmd, env)
    if rc != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def inspect_colab_live(colab_bin: str | None, colab_cfg: str | None, env: dict[str, str]) -> dict[str, Any]:
    """Optionally query live Colab sessions and compute-unit usage."""
    if not colab_bin:
        return {"skipped": True, "reason": "colab binary not found"}
    base = [colab_bin]
    if colab_cfg:
        base.extend(["--config", colab_cfg])
    rc_sess, out_sess, err_sess = run_cmd([*base, "sessions"], env, timeout=20)
    rc_usage, out_usage, err_usage = run_cmd([*base, "usage"], env, timeout=20)
    return {
        "sessions_rc": rc_sess,
        "sessions_output": out_sess or err_sess,
        "usage_rc": rc_usage,
        "usage_output": out_usage or err_usage,
    }


def build_recommendations(report: dict[str, Any]) -> list[str]:
    """Generate actionable remediation hints from inspection results."""
    hints: list[str] = []
    tools = report["tools"]
    if not tools["colab"] and not tools["colab-mcp"]:
        hints.append("Install google-colab-cli (`uv tool install google-colab-cli`) or colab-mcp.")
    if not tools["rclone"]:
        hints.append("Install rclone (`brew install rclone` or https://rclone.org/install.sh).")
    if report["isolation"]["home_isolated"]:
        hints.append(
            "Isolated $HOME detected: pass --rclone-config / RCLONE_CONFIG and --colab-config "
            "explicitly if your configs live under the OS user home."
        )
    if tools["rclone"] and not report["rclone"]["remotes"]:
        hints.append("No rclone remotes found in active config; run `rclone config` with BYOC OAuth.")
    return hints


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight check for AGY Colab + rclone + Drive workflow.")
    parser.add_argument("--rclone-config", help="Path to rclone config file")
    parser.add_argument("--remote", default=os.environ.get("COLAB_RCLONE_REMOTE"), help="Expected rclone remote name")
    parser.add_argument("--colab-config", help="Path to colab-cli sessions.json")
    parser.add_argument("--drive-root", default=os.environ.get("COLAB_DRIVE_ROOT", "colab"), help="Drive root folder")
    parser.add_argument("--check-colab-live", action="store_true", help="Query `colab sessions` and `colab usage`")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser.parse_args()


def print_human(report: dict[str, Any]) -> None:
    """Print concise human-readable diagnostic summary."""
    print("=== Colab + rclone + Drive Preflight ===")
    iso = report["isolation"]
    print(f"HOME isolated : {iso['home_isolated']} (env={iso['env_home']}, os={iso['os_home']})")
    for name, path in report["tools"].items():
        print(f"Tool {name:<9}: {path or 'MISSING'}")
    rc = report["rclone"]
    print(f"rclone config : {rc['active_config'] or 'NONE'} (candidates={len(rc['candidates'])})")
    print(f"rclone remotes: {', '.join(rc['remotes']) or 'NONE'}")
    if rc["requested_remote"]:
        print(f"Target remote : {rc['requested_remote']} (found={rc['requested_remote_found']})")
    print(f"colab config  : {report['colab_config']['active']} (exists={report['colab_config']['exists']})")
    if report["colab_live"]:
        live = report["colab_live"]
        print(f"colab sessions: rc={live.get('sessions_rc')} | {live.get('sessions_output', '')[:160]}")
        print(f"colab usage   : rc={live.get('usage_rc')} | {live.get('usage_output', '')[:160]}")
    if report["recommendations"]:
        print("\nRecommendations:")
        for item in report["recommendations"]:
            print(f"  - {item}")


def main() -> int:
    args = parse_args()
    env_home, os_home, is_isolated = get_homes()
    env = build_search_env(env_home, os_home)
    tools = {name: shutil.which(name, path=env["PATH"]) for name in ("colab", "colab-mcp", "rclone", "uv")}
    active_rc, rc_candidates = discover_rclone_configs(args.rclone_config, env_home, os_home)
    remotes = list_rclone_remotes(tools["rclone"], active_rc, env)
    target = (args.remote.rstrip(":") + ":") if args.remote else None
    colab_cfg = discover_colab_config(args.colab_config, env_home, os_home)
    live = inspect_colab_live(tools["colab"], colab_cfg["active"], env) if args.check_colab_live else None
    report: dict[str, Any] = {
        "isolation": {"env_home": str(env_home), "os_home": str(os_home), "home_isolated": is_isolated},
        "tools": tools,
        "drive_root": args.drive_root,
        "rclone": {
            "active_config": active_rc,
            "candidates": rc_candidates,
            "remotes": remotes,
            "requested_remote": target,
            "requested_remote_found": bool(target and target in remotes) if target else None,
        },
        "colab_config": colab_cfg,
        "colab_live": live,
    }
    report["recommendations"] = build_recommendations(report)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_human(report)
    ok = bool(tools["rclone"] and (tools["colab"] or tools["colab-mcp"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
