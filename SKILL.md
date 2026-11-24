---
name: agy-create-colab-notebook
description: >-
  End-to-end standard operating procedure for running GPU/TPU workloads on Consumer
  Google Colab from Antigravity (AGY App, IDE, and CLI) paired with rclone and
  Google Drive staging. Covers compute-pool routing, ToS boundaries, multi-profile
  and isolated-HOME setup, google-colab-cli headless execution, colab-mcp interactive
  notebook bridging, Drive-to-local staging to avoid FUSE I/O bottlenecks, 750 GB/day
  rclone throttling, and mandatory try/finally runtime teardown. Activate whenever
  a task involves Google Colab, GPU/TPU offload, model training or fine-tuning,
  large-scale batch inference/embedding/OCR/Whisper, CUDA/Triton/vLLM debugging,
  google-colab-cli, colab-mcp, or syncing datasets and artifacts with Google Drive
  via rclone.
---

# Colab + rclone + Google Drive Workflow (`agy-create-colab-notebook`)

Run GPU/TPU compute jobs on Consumer Google Colab from Antigravity (AGY App, IDE, or CLI) using `google-colab-cli` or `colab-mcp`, backed by `rclone` and Google Drive for dataset staging and artifact persistence.

This skill is **tier-agnostic and portable**: it works across Colab Free, Pay-As-You-Go, Google AI Pro, and Google AI Ultra (5x/20x), on macOS and Linux, and in both standard and multi-profile isolated AGY setups.

## Core Principles

1. **Billing Isolation**: Always use **Consumer Colab** official channels (`google-colab-cli`, `colab-mcp`, or VS Code Colab extension -> `colaboratory.googleapis.com`). Never accidentally invoke GCP **Colab Enterprise / Vertex AI** (`aiplatform.googleapis.com`), which bills independently to a GCP billing account and does not use consumer Compute Units (CCUs).
2. **Zero-Waste Cost Stop**: Every paid GPU/TPU script must wrap `from google.colab import runtime; runtime.unassign()` inside `try ... finally:`. Prefer ephemeral `colab run` (without `--keep`) over long-lived sessions.
3. **Never Train Directly on Drive FUSE**: Store datasets and outputs in Google Drive, but bulk-copy inputs into Colab local disk (`/content/work`) before reading. Random small-file I/O over Drive FUSE stalls GPU utilization.
4. **Zero-Error Smoke Gate**: Validate code syntax, DataLoader, and a 1-step forward/backward pass locally or on CPU/T4 before provisioning L4, G4, A100, H100, or TPU runtimes.
5. **Respect Upload & ToS Limits**: Honor Google Drive's `750 GB / 24h` rolling upload limit (`--max-transfer 740G` or `--bwlimit 8.5M`), keep desktop Drive clients on Streaming (never multi-TB Mirroring), and never run ToS-prohibited workloads.

---

## Compute Routing Gate (Run on Colab or Not?)

| Workload Type | Route To | Reason |
| :--- | :--- | :--- |
| Model training / fine-tuning (LoRA, QLoRA, CV, Audio, Multimodal) | **Colab GPU/TPU** | Uses consumer CCUs; supports up to 24h background runs on eligible tiers |
| Large offline batch ML (Embedding, Reranker, OCR, Whisper, offline eval) | **Colab GPU/TPU** | Offloads compute from local CPU and avoids burning AGY chat/agent pools |
| CUDA / Triton / vLLM kernel debugging & benchmarking | **Colab GPU** | Provides real NVIDIA GPU environment (especially useful on macOS) |
| JAX / Flax / PyTorch TPU numerical simulation | **Colab TPU (`V5E1`/`V6E1`)** | High throughput at relatively low CU/hr |
| Routine coding, multi-file refactoring, unit tests | **Local + AGY / Swarm** | Keeps GPU CCUs for actual accelerator workloads |
| Large text/LLM batch extraction, classification, eval (`<= 2 GB` JSONL) | **Gemini Batch API** | Decoupled 24h async API (50% discount); no GPU VM overhead |
| Small-sample debug, syntax check, DataLoader verification | **Local or Free CPU/T4** | Eliminate bugs at 0 CU cost before allocating paid accelerators |
| 24/7 web servers, proxies, tunneling, BT/P2P, crypto mining, media hosting, anti-idle hacks | **FORBIDDEN (ToS)** | Violates Colab Terms of Service; risks account suspension |

---

## Channel Selection (`google-colab-cli` vs `colab-mcp`)

| Criterion | `google-colab-cli` (`colab`) | `colab-mcp` (MCP Server) |
| :--- | :--- | :--- |
| **Best For** | Headless automation, batch scripts, reproducible pipelines | Interactive notebook co-editing, step-by-step cell inspection |
| **Execution Mode** | `colab run` (ephemeral, auto-teardown) or `colab new/exec/stop` | Browser-bridged WebSocket proxy (`open_colab_browser_connection`) |
| **Human Interaction** | None after OAuth/ADC login | User confirms browser connection within 60s |
| **Runtime Teardown** | Automatic on `colab run` exit; `colab stop` for persistent sessions | No standalone `DeleteRuntime` tool in v1.0.x; **must run `runtime.unassign()` in final cell** |
| **Headless Redlines** | Never run interactive `colab ssh` (without `-- CMD`), `colab repl`, or interactive `colab drivemount` | Do not expect background connection without an active browser tab |

See [references/colab-cli-and-mcp.md](./references/colab-cli-and-mcp.md) for full CLI flags, MCP tools, and CU burn rate tables.

---

## 6-Phase Standard Operating Procedure (SOP)

### Phase 0: Environment & Isolation Preflight

Before touching Drive or allocating any VM, run [scripts/preflight_check.py](./scripts/preflight_check.py):

```bash
python3 <skill_dir>/scripts/preflight_check.py --check-colab-live
```

Pass explicit overrides when running inside an isolated AGY profile (`$HOME` overridden) or with custom config paths:

```bash
python3 <skill_dir>/scripts/preflight_check.py \
  --rclone-config /path/to/rclone.conf \
  --remote my-gdrive \
  --colab-config /path/to/sessions.json \
  --check-colab-live
```

**Gate Criteria**:
- `rclone` and either `colab` or `colab-mcp` are reachable (auto-checks standard Homebrew / `~/.local/bin` paths).
- Target `rclone` remote resolves cleanly without exposing secrets.
- `colab usage` confirms sufficient CCU balance; `colab sessions` shows no forgotten leaked VMs.
- If running in a multi-profile / isolated `$HOME` environment and tools miss configs, consult [references/setup-and-isolation.md](./references/setup-and-isolation.md).

### Phase 1: Routing, Scope & Compliance Confirmation

1. Confirm the task passes the **Compute Routing Gate** above and has zero ToS violations.
2. Select target accelerator (`T4`, `L4`, `G4`, `A100`, `H100`, `V5E1`, `V6E1`) and estimate total CU budget (`estimated_hours * cu_per_hour`) against current `colab usage`.
3. Choose channel: default to `google-colab-cli` for automated batch scripts; choose `colab-mcp` only when live notebook cell collaboration is explicitly needed.

### Phase 2: Data Staging via `rclone` + Google Drive

Standardize cloud layout under a configurable root (default `colab`):

```text
<remote>:<drive_root>/
├── notebooks/   # Versioned .ipynb / entry scripts
├── datasets/    # Immutable input archives (.tar / .tar.gz / parquet)
├── runs/        # Per-run outputs: runs/<run_id>/{checkpoints,artifacts,manifest.json}
└── cache/       # Reusable uv/pip wheels or pretrained model caches
```

Use [scripts/rclone_stage.py](./scripts/rclone_stage.py) (safe defaults: `--max-transfer 740G`, `--drive-stop-on-upload-limit`):

```bash
# 1. Initialize remote directory structure
python3 <skill_dir>/scripts/rclone_stage.py init --remote <remote> --drive-root colab

# 2. Push dataset (use --pack-tar for directories with many small files!)
python3 <skill_dir>/scripts/rclone_stage.py push \
  --remote <remote> --drive-root colab \
  --src ./local_dataset --dest datasets/my_dataset --pack-tar --dry-run

# Remove --dry-run once verified
```

See [references/rclone-drive-pipeline.md](./references/rclone-drive-pipeline.md) for BYOC OAuth setup, `rclone crypt` archival, 24/7 `--bwlimit 8.5M` mode, and macOS SSD TBW rules.

### Phase 3: Zero-Error Smoke Gate

1. Build the job script from [resources/colab_job_template.py](./resources/colab_job_template.py).
2. Verify all 4 mandatory script invariants before allocating a paid GPU/TPU:
   - Bulk-copies dataset from Drive into `/content/work` before training/inference.
   - Prints progress periodically with `flush=True` (prevents `colab run`/`exec` default `30.0s` idle-output timeout).
   - Writes final artifacts + `manifest.json` back to `<drive_root>/runs/<run_id>/`.
   - Wraps `from google.colab import runtime; runtime.unassign()` inside `try ... finally:`.
3. Run a local syntax check (`python3 -m py_compile job.py`) and a minimal smoke run locally or on a cheap/CPU/T4 runtime with `--timeout 0` (or generous timeout) until exit code is `0`.

### Phase 4: Remote Execution on Colab

#### Path A: Headless Automation via `google-colab-cli` (Recommended)

- **Single-script ephemeral job (preferred — automatic teardown)**:
  ```bash
  colab run --gpu L4 --timeout 0 job.py \
    --drive-root colab --dataset datasets/my_dataset.tar.gz --run-id run_20261124_01
  ```
- **Multi-step stateful session (when installing deps / uploading helper modules)**:
  ```bash
  SESSION="job-$(date +%Y%m%d-%H%M%S)"
  trap 'colab stop -s "$SESSION" >/dev/null 2>&1 || true' EXIT
  colab new -s "$SESSION" --gpu A100 --high-mem
  colab install -s "$SESSION" -r requirements.txt
  colab upload -s "$SESSION" ./src.tar.gz /content/src.tar.gz
  colab exec -s "$SESSION" -f job.py --timeout 0
  colab download -s "$SESSION" /content/work/summary.json ./summary.json
  colab stop -s "$SESSION"
  ```
- Always set `--timeout 0` (or explicit large value) on `colab run` / `colab exec` for long steps with quiet stdout; default is `30.0` seconds.

#### Path B: Interactive Notebook via `colab-mcp`

1. Call `open_colab_browser_connection` and wait for the user to approve the browser tab (60s window).
2. Inspect existing structure with `get_cells`.
3. Add or update cells with `add_code_cell` / `update_cell`:
   - Cell 1: Verify GPU/TPU (`nvidia-smi` or JAX device check) and stage data from Drive to `/content/work`.
   - Cell 2..N: Execute workload, saving checkpoints and metrics to Drive `runs/<run_id>/`.
   - Final Cell (or `finally:` block): Sync logs and call `from google.colab import runtime; runtime.unassign()`.
4. Run cells sequentially via `run_code_cell`, inspecting outputs after each critical stage.

### Phase 5: Artifact Retrieval, Verification & Teardown Audit

1. Pull artifacts back to local workspace and verify hashes:
   ```bash
   python3 <skill_dir>/scripts/rclone_stage.py pull \
     --remote <remote> --drive-root colab \
     --src runs/<run_id> --dest ./outputs/<run_id>

   python3 <skill_dir>/scripts/rclone_stage.py verify \
     --remote <remote> --drive-root colab \
     --remote-subpath runs/<run_id> --local-dir ./outputs/<run_id>
   ```
2. **Mandatory Teardown Audit**:
   ```bash
   colab sessions   # Must show zero leftover paid runtimes
   colab usage      # Record remaining CCU balance
   ```
3. If temporary staging archives were deleted on Drive, remind the user that Drive Trash retains deleted files for **30 days** and counts them against quota until emptied.

---

## Bundled References, Scripts & Templates

- **Setup, Auth & Multi-Profile Isolation**: [references/setup-and-isolation.md](./references/setup-and-isolation.md)
- **Colab CLI & MCP Cookbook + CU Rates**: [references/colab-cli-and-mcp.md](./references/colab-cli-and-mcp.md)
- **rclone & Google Drive Pipeline Guide**: [references/rclone-drive-pipeline.md](./references/rclone-drive-pipeline.md)
- **Preflight Diagnostic Script**: [scripts/preflight_check.py](./scripts/preflight_check.py)
- **Safe rclone Staging Wrapper**: [scripts/rclone_stage.py](./scripts/rclone_stage.py)
- **Colab Job Template (with `try/finally` Teardown)**: [resources/colab_job_template.py](./resources/colab_job_template.py)
- **Generic `colab-mcp` Config Example**: [resources/mcp_config.example.json](./resources/mcp_config.example.json)
