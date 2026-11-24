#!/usr/bin/env python3
"""Universal Consumer Colab job template with local /content/work staging and
mandatory try/finally runtime.unassign() cost-stop guard.

Supports both:
  1. Mounted Google Drive (/content/drive/MyDrive/<drive_root>) in interactive/MCP runs.
  2. Headless rclone transfer (pass RCLONE_CONF_B64 + --remote) in `colab run` pipelines.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path
from typing import Any

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")


def log(msg: str) -> None:
    """Emit timestamped, flushed logs to prevent colab-cli 30s idle timeout."""
    ts = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts} UTC] {msg}", flush=True)


def write_ephemeral_rclone_conf() -> Path | None:
    """Decode RCLONE_CONF_B64 into a randomized mode-0600 temp file when headless."""
    raw_b64 = os.environ.get("RCLONE_CONF_B64")
    if not raw_b64:
        return None
    fd, raw_path = tempfile.mkstemp(prefix="colab-rclone-", suffix=".conf", dir="/tmp")
    with os.fdopen(fd, "wb") as handle:
        handle.write(base64.b64decode(raw_b64))
    return Path(raw_path)


def cleanup_ephemeral_conf(conf_path: Path | None) -> None:
    if conf_path and conf_path.exists():
        conf_path.unlink(missing_ok=True)


def unassign_colab_runtime() -> None:
    """Release paid Colab GPU/TPU immediately when running inside Colab."""
    try:
        from google.colab import runtime  # type: ignore
        log("Releasing Colab runtime via runtime.unassign()...")
        runtime.unassign()
    except ImportError:
        log("Not inside Google Colab runtime; skipping runtime.unassign().")
    except Exception as exc:
        log(f"Warning: runtime.unassign() failed: {exc}")


def stage_input_to_local(
    args: argparse.Namespace, data_dir: Path, rclone_conf: Path | None
) -> None:
    """Bulk-copy dataset into fast local disk (/content/work/data) before compute."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not args.dataset:
        log("No --dataset specified; skipping input staging.")
        return
    if rclone_conf and args.remote:
        src = f"{args.remote.rstrip(':')}:{args.drive_root.strip('/')}/{args.dataset.lstrip('/')}"
        log(f"Staging via rclone: {src} -> {data_dir}")
        subprocess.run(["rclone", "--config", str(rclone_conf), "copy", src, str(data_dir)], check=True)
    else:
        drive_src = Path(args.drive_mount) / "MyDrive" / args.drive_root / args.dataset
        log(f"Staging from mounted Drive: {drive_src} -> {data_dir}")
        if drive_src.is_dir():
            shutil.copytree(drive_src, data_dir / drive_src.name, dirs_exist_ok=True)
        else:
            shutil.copy2(drive_src, data_dir / drive_src.name)
    extract_archives(data_dir)


def safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract tar members safely without path traversal across Python 3.9–3.14+."""
    if sys.version_info >= (3, 12):
        tar.extractall(path=dest, filter="data")
        return
    base = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"Unsafe tar member path: {member.name}")
    tar.extractall(path=dest)


def extract_archives(data_dir: Path) -> None:
    """Extract supported tar archives locally to avoid Drive FUSE overhead."""
    for archive in sorted(data_dir.iterdir()):
        if archive.is_file() and archive.name.endswith(TAR_SUFFIXES):
            log(f"Extracting {archive.name} into {data_dir}...")
            with tarfile.open(archive, "r:*") as tar:
                safe_extract_tar(tar, data_dir)


def sync_outputs_to_drive(
    args: argparse.Namespace, out_dir: Path, rclone_conf: Path | None
) -> None:
    """Write final checkpoints, outputs, and manifest.json back to Drive."""
    if not out_dir.exists():
        return
    if rclone_conf and args.remote:
        dst = f"{args.remote.rstrip(':')}:{args.drive_root.strip('/')}/runs/{args.run_id}"
        log(f"Syncing outputs via rclone -> {dst}")
        subprocess.run(
            ["rclone", "--config", str(rclone_conf), "copy", str(out_dir), dst,
             "--max-transfer", "740G", "--drive-stop-on-upload-limit"],
            check=True,
        )
        return
    drive_dst = Path(args.drive_mount) / "MyDrive" / args.drive_root / "runs" / args.run_id
    if Path(args.drive_mount).exists():
        log(f"Syncing outputs to mounted Drive -> {drive_dst}")
        drive_dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out_dir, drive_dst, dirs_exist_ok=True)
    else:
        log(f"Drive mount not found ({args.drive_mount}); outputs kept at {out_dir}")


def run_workload(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Replace this function with your training, fine-tuning, or batch inference logic."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Running workload with staged inputs in {data_dir}...")
    files = sorted(str(p.relative_to(data_dir)) for p in data_dir.rglob("*") if p.is_file())
    summary = {"status": "ok", "staged_file_count": len(files), "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    (out_dir / "manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"Workload completed; wrote manifest.json ({len(files)} input files seen).")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Universal Colab job template.")
    parser.add_argument("--drive-root", default=os.environ.get("COLAB_DRIVE_ROOT", "colab"))
    parser.add_argument("--drive-mount", default="/content/drive")
    parser.add_argument("--remote", default=os.environ.get("COLAB_RCLONE_REMOTE"))
    parser.add_argument("--dataset", default="", help="Relative path under <drive_root> (e.g. datasets/sample.tar.gz)")
    parser.add_argument("--run-id", default=dt.datetime.now(dt.timezone.utc).strftime("run_%Y%m%d_%H%M%S"))
    parser.add_argument("--work-dir", default="/content/work")
    parser.add_argument("--no-unassign", action="store_true", help="Skip runtime.unassign() (local testing only)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    work_dir = Path(args.work_dir)
    data_dir = work_dir / "data"
    out_dir = work_dir / "out" / args.run_id
    rclone_conf = write_ephemeral_rclone_conf()
    try:
        stage_input_to_local(args, data_dir, rclone_conf)
        run_workload(data_dir, out_dir)
        sync_outputs_to_drive(args, out_dir, rclone_conf)
        return 0
    except Exception:
        traceback.print_exc()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            sync_outputs_to_drive(args, out_dir, rclone_conf)
        except Exception as sync_exc:
            log(f"Warning: failed to upload error.log: {sync_exc}")
        raise
    finally:
        cleanup_ephemeral_conf(rclone_conf)
        if not args.no_unassign:
            unassign_colab_runtime()


if __name__ == "__main__":
    sys.exit(main())
