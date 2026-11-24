# rclone + Google Drive Staging & Archival Pipeline Guide

Standardized data movement architecture between local workstations/NAS, Google Drive, and Colab ephemeral VMs.

---

## 1. End-to-End Data Flow Architecture

```text
[Local Machine / NAS]
   │  (1) Pack small files (.tar/.tar.gz) + rclone copy (throttled <= 740G/day)
   ▼
[Google Drive: <drive_root>/]
   ├── datasets/   (immutable input archives)
   ├── cache/      (reusable uv/pip wheels, HuggingFace/model caches)
   ├── notebooks/  (versioned notebooks / scripts)
   └── runs/<id>/  (outputs, checkpoints, manifest.json)
   │  (2) Bulk copy/extract ONCE at job start (NO random reads over Drive FUSE)
   ▼
[Colab VM Local Disk: /content/work/]
   ├── data/       (fast local SSD reads during training/inference)
   └── out/        (local checkpoints & metrics)
   │  (3) Sync final artifacts + manifest.json back to Drive runs/<id>/
   ▼
[Google Drive: <drive_root>/runs/<id>/]
   │  (4) rclone copy + rclone check back to local workstation
   ▼
[Local ./outputs/<id>/]
```

### Why Staging to `/content/work` Matters

Google Drive mounted via FUSE (`/content/drive/MyDrive/...`) translates every `open()`, `stat()`, and small-file read into remote HTTP API calls:
- Reading thousands of images, audio clips, or JSONL chunks directly from Drive FUSE drops GPU utilization below 10% and triggers Drive API rate limits (`403 userRateLimitExceeded`).
- **Rule**: Pack many-file datasets into `.tar` / `.tar.gz` / WebDataset shards / Parquet before uploading. At Colab startup, copy the single archive to `/content/work`, extract locally, train at full disk speed, and write only checkpoints + final outputs back to Drive.

---

## 2. Google Drive Hard Quotas & Safe `rclone` Modes

| Limit / Constraint | Value | Operational Consequence |
| :--- | :--- | :--- |
| **Rolling Upload Cap** | `750 GB / 24h` per Google account | Exceeding causes `userRateLimitExceeded` failures for up to 24h |
| **Single File Max Size** | `5 TB` (non-Google Docs files) | Large tarballs and model checkpoints are well supported |
| **Drive API QPS** | Throttled on small-file bursts | Pack small files into archives; avoid `--transfers > 8` on small files |
| **Trash Retention** | `30 days` (Drive, Gmail, Photos) | Deleted staging files **still consume quota** until Trash is permanently emptied |

### Safe `rclone` Transfer Modes

#### Mode A: Daily Batch Cap (Default — Reserves ~10 GB for Normal Sync)

```bash
rclone copy /path/to/local_data gdrive:colab/datasets/my_dataset \
  --max-transfer 740G \
  --drive-stop-on-upload-limit \
  --transfers 4 \
  --checkers 8 \
  --fast-list \
  --progress
```

#### Mode B: 24/7 Steady-State Throttling (`8.5 MiB/s ≈ 734 GB / 24h`)

Ideal for multi-terabyte NAS or server background archival that runs continuously without ever hitting the 750 GB daily wall:

```bash
rclone copy /volume/archive gdrive-crypt:cold-archive \
  --bwlimit 8.5M \
  --transfers 4 \
  --checkers 8 \
  --progress
```

#### Integrity Verification After Push or Pull

```bash
rclone check ./outputs/run_001 gdrive:colab/runs/run_001 --one-way
```

---

## 3. Accessing Drive Inside Colab VMs (Two Methods)

### Method 1: Native Drive Mount (Best for `colab-mcp` / Browser-Authorized Sessions)

```python
from google.colab import drive
drive.mount("/content/drive", force_remount=False)
```

Then bulk-copy from `/content/drive/MyDrive/<drive_root>/datasets/...` into `/content/work/data/`.

### Method 2: Headless `rclone` Inside Colab VM (Best for `colab run` Headless Pipelines)

Because headless `colab run` sessions cannot complete interactive browser OAuth prompts for `drive.mount()`, use `rclone` inside the Colab VM with a scoped config passed securely at runtime (never committed to Git):

1. Keep a dedicated, minimally scoped `rclone` config (or `drive.file` scope) outside your repository.
2. Upload it transiently to `/tmp/rclone.conf` (chmod `600`) in a `colab new/exec` session, OR pass base64-encoded config via `--env RCLONE_CONF_B64=...` to `colab run`, decode to `/tmp/rclone.conf` (`0600`), run `rclone copy`, and shred/delete `/tmp/rclone.conf` in `finally:`.
3. Install `rclone` inside Colab only if missing (`which rclone || curl -sS https://rclone.org/install.sh | bash`), or cache the static `rclone` Linux binary inside Drive/release storage for 1-second startup.

> **Security Hygiene**: Never hardcode OAuth tokens or paste `rclone.conf` into notebooks, repo files, or shared logs. Always delete `/tmp/rclone.conf` in `finally:`.

---

## 4. Wheel & Model Caching Strategy

Re-installing PyTorch extras, FlashAttention, or downloading 10–30 GB HuggingFace weights on every fresh Colab VM burns CCUs before training even starts:

- Set `UV_CACHE_DIR=/content/work/uv-cache` or keep prebuilt `.whl` files in `<drive_root>/cache/wheels/` and copy once at startup.
- For HuggingFace models, sync model snapshots between `<drive_root>/cache/hf/` and `/content/work/hf_cache` (`HF_HOME=/content/work/hf_cache`) when Drive-to-Colab throughput beats upstream download or avoids HuggingFace rate limits.
- Pin exact versions in `requirements.txt` and prefer `uv pip install --system` over `pip install` for 5–10x faster VM setup.

---

## 5. Encrypted Cold Archival & Desktop SSD Protection

### 5.1 End-to-End Encrypted Cold Backup (`rclone crypt`)

For sensitive backups stored on large Google Drive plans (e.g., 5 TB / 20 TB / 30 TB):
1. Wrap the base `drive` remote with an `rclone crypt` remote (encrypts filenames and file contents client-side).
2. Schedule nightly uploads from a NAS or always-on host using Mode A (`--max-transfer 740G`) or Mode B (`--bwlimit 8.5M`).
3. Periodically run `rclone check` to verify remote hash integrity.

### 5.2 Desktop Drive Client Rules (macOS & Windows)

- **Keep Drive Desktop in Streaming Mode**: Never enable **Mirroring** on multi-terabyte folders — mirroring writes full copies to local SSD and accelerates SSD TBW wear.
- **Keep Active Projects Off Drive Cache**: Even in Streaming mode, opening large video/code/dataset files inside `~/Library/CloudStorage/` hydrates them into local APFS cache and causes write amplification. Work on local SSD or external NVMe, then archive finished artifacts to Drive via `rclone`.
- **Never Sync Time Machine `.sparsebundle` Directly**: Concurrent small-band files and lock semantics corrupt sparsebundles and trigger infinite re-uploads. Create deduplicated snapshots with `restic` or `borg` first, then sync the snapshot repo via `rclone`.
