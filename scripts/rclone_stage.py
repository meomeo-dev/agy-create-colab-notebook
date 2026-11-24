#!/usr/bin/env python3
"""Safe, throttled rclone staging wrapper for Google Drive + Colab workflows."""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def resolve_homes() -> tuple[Path, Path]:
    env_home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    try:
        os_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        os_home = env_home
    return env_home, os_home


def find_rclone(env_home: Path, os_home: Path) -> str:
    """Locate rclone across PATH and common macOS/Linux install paths."""
    extras = ["/opt/homebrew/bin", "/usr/local/bin", str(env_home / ".local/bin"), str(os_home / ".local/bin")]
    search = os.pathsep.join([os.environ.get("PATH", ""), *extras])
    binary = shutil.which("rclone", path=search)
    if not binary:
        raise FileNotFoundError("rclone not found. Install via `brew install rclone` or https://rclone.org/install.sh")
    return binary


def resolve_rclone_config(explicit: str | None, env_home: Path, os_home: Path) -> str | None:
    """Resolve rclone config file across explicit flags, env vars, and isolated/OS homes."""
    for candidate in (explicit, os.environ.get("COLAB_RCLONE_CONFIG"), os.environ.get("RCLONE_CONFIG")):
        if candidate:
            p = Path(candidate).expanduser()
            if p.is_file():
                return str(p)
            raise FileNotFoundError(f"Configured rclone config not found: {p}")
    for base in (env_home, os_home):
        default_conf = base / ".config" / "rclone" / "rclone.conf"
        if default_conf.is_file():
            return str(default_conf)
    return None


def remote_path(remote: str, drive_root: str, subpath: str = "") -> str:
    """Construct normalized `<remote>:<root>/<subpath>` target."""
    clean_remote = remote.rstrip(":")
    parts = [p.strip("/") for p in (drive_root, subpath) if p and p.strip("/")]
    return f"{clean_remote}:{'/'.join(parts)}"


def transfer_flags(mode: str, dry_run: bool) -> list[str]:
    """Build safe Drive transfer flags honoring the 750 GB / 24h rolling limit."""
    flags = ["--transfers", "4", "--checkers", "8", "--progress"]
    if mode == "steady":
        flags.extend(["--bwlimit", "8.5M"])
    else:
        flags.extend(["--max-transfer", "740G", "--drive-stop-on-upload-limit"])
    if dry_run:
        flags.append("--dry-run")
    return flags


def run_rclone(binary: str, config: str | None, args: list[str]) -> int:
    cmd = [binary]
    if config:
        cmd.extend(["--config", config])
    cmd.extend(args)
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def cmd_init(args: argparse.Namespace, binary: str, config: str | None) -> int:
    for sub in ("notebooks", "datasets", "runs", "cache"):
        target = remote_path(args.remote, args.drive_root, sub)
        rc = run_rclone(binary, config, ["mkdir", target])
        if rc != 0:
            return rc
    return 0


def cmd_push(args: argparse.Namespace, binary: str, config: str | None) -> int:
    src = Path(args.src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source path not found: {src}")
    flags = transfer_flags(args.mode, args.dry_run)
    target = remote_path(args.remote, args.drive_root, args.dest)
    if args.pack_tar and src.is_dir():
        with tempfile.TemporaryDirectory(prefix="colab-stage-", dir="/tmp") as tmpdir:
            archive = Path(tmpdir) / f"{src.name}.tar.gz"
            print(f"Packing directory {src} -> {archive}", flush=True)
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(src, arcname=src.name)
            return run_rclone(binary, config, ["copy", str(archive), target, *flags])
    return run_rclone(binary, config, ["copy", str(src), target, *flags])


def cmd_pull(args: argparse.Namespace, binary: str, config: str | None) -> int:
    dest = Path(args.dest).expanduser().resolve()
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    src = remote_path(args.remote, args.drive_root, args.src)
    flags = transfer_flags(args.mode, args.dry_run)
    return run_rclone(binary, config, ["copy", src, str(dest), *flags])


def cmd_verify(args: argparse.Namespace, binary: str, config: str | None) -> int:
    local_dir = Path(args.local_dir).expanduser().resolve()
    if not local_dir.is_dir():
        raise FileNotFoundError(f"Local directory not found: {local_dir}")
    target = remote_path(args.remote, args.drive_root, args.remote_subpath)
    return run_rclone(binary, config, ["check", str(local_dir), target, "--one-way"])


def cmd_ls(args: argparse.Namespace, binary: str, config: str | None) -> int:
    target = remote_path(args.remote, args.drive_root, args.subpath)
    return run_rclone(binary, config, ["lsf", target])


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--remote", default=os.environ.get("COLAB_RCLONE_REMOTE"), required=not os.environ.get("COLAB_RCLONE_REMOTE"), help="rclone remote name (or set COLAB_RCLONE_REMOTE)")
    common.add_argument("--drive-root", default=os.environ.get("COLAB_DRIVE_ROOT", "colab"), help="Drive workspace root folder")
    common.add_argument("--rclone-config", help="Path to rclone config file")
    parser = argparse.ArgumentParser(description="Safe rclone staging helper for Colab + Google Drive.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", parents=[common], help="Create <drive_root>/{notebooks,datasets,runs,cache}")
    p_push = sub.add_parser("push", parents=[common], help="Copy local dataset/script to Drive")
    p_push.add_argument("--src", required=True, help="Local file or directory")
    p_push.add_argument("--dest", required=True, help="Subpath under drive_root (e.g. datasets/proj)")
    p_push.add_argument("--pack-tar", action="store_true", help="Pack directory into .tar.gz in /tmp before upload")
    p_push.add_argument("--mode", choices=("batch", "steady"), default="batch")
    p_push.add_argument("--dry-run", action="store_true")
    p_pull = sub.add_parser("pull", parents=[common], help="Copy remote artifacts from Drive to local")
    p_pull.add_argument("--src", required=True, help="Subpath under drive_root (e.g. runs/run_001)")
    p_pull.add_argument("--dest", required=True, help="Local destination directory")
    p_pull.add_argument("--mode", choices=("batch", "steady"), default="batch")
    p_pull.add_argument("--dry-run", action="store_true")
    p_ver = sub.add_parser("verify", parents=[common], help="Run one-way rclone check against Drive")
    p_ver.add_argument("--local-dir", required=True)
    p_ver.add_argument("--remote-subpath", required=True)
    p_ls = sub.add_parser("ls", parents=[common], help="List entries under remote drive_root")
    p_ls.add_argument("--subpath", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_home, os_home = resolve_homes()
    binary = find_rclone(env_home, os_home)
    config = resolve_rclone_config(args.rclone_config, env_home, os_home)
    handlers = {"init": cmd_init, "push": cmd_push, "pull": cmd_pull, "verify": cmd_verify, "ls": cmd_ls}
    return handlers[args.command](args, binary, config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
