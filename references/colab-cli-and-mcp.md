# Colab CLI & MCP Operational Cookbook

Reference manual for operating Consumer Google Colab from Antigravity via `google-colab-cli` (`colab`) and `colab-mcp`, including accelerator Compute Unit (CU) reference rates and cost-control safeguards.

---

## 1. Consumer Colab vs Colab Enterprise (Billing Boundary)

| Dimension | Consumer Colab (Use This) | Colab Enterprise / Vertex AI (Do NOT Mix) |
| :--- | :--- | :--- |
| **API Endpoint** | `colaboratory.googleapis.com` | `aiplatform.googleapis.com` |
| **Tools** | `google-colab-cli`, `colab-mcp`, VS Code `Google.colab`, `colab.research.google.com` | GCP Console, `gcloud`, Vertex AI Workbench / Colab Enterprise API |
| **Billing Source** | Personal Colab Free / PAYG / AI Pro / AI Ultra CCUs | GCP Cloud Billing Account (Compute Engine VM + GPU + boot disk PAYG) |
| **Storage Default** | Google Drive (`/content/drive`) + ephemeral `/content` | GCS Buckets (`gs://...`) |

**Rule**: Never substitute Vertex AI / Colab Enterprise APIs when the user expects to use their Consumer Colab subscription or Compute Units.

---

## 2. Subscription Tiers & Accelerator CU Burn Rates

> Reference figures verified **2026-11** (subject to Google dynamic scheduling and machine-shape changes; always check `colab usage` before launching heavy jobs).

### Consumer Tiers Overview

| Plan | Monthly CCUs | Background Execution | Notes |
| :--- | :--- | :--- | :--- |
| **Colab Free** | 0 (dynamic free T4/CPU quota) | No | Best for smoke tests; subject to idle disconnects |
| **Pay-As-You-Go (PAYG)** | Purchased packs (e.g. `~$10 / 100 CCUs`) | Standard | Compute Units expire per Google PAYG terms |
| **Google AI Pro** | `200 CCUs/mo` | Standard | Reset monthly on billing date; no rollover |
| **Google AI Ultra 5x** | `1,000 CCUs/mo` | Up to 24h | Owner-only (not shared with family group) |
| **Google AI Ultra 20x** | `2,000 CCUs/mo` | Up to 24h | Owner-only; reset monthly on billing date; no rollover |

### Typical Accelerator Burn Rates (`CU/hr`, approximate)

| Accelerator | CLI Flag | Typical `CU/hr` | Best Workload Fit |
| :--- | :--- | :--- | :--- |
| **CPU** | *(omit `--gpu`/`--tpu`)* | `~0.07–0.1+` (or free) | Data prep, smoke checks, light CPU ETL |
| **T4** | `--gpu T4` | `~1.1–1.6` (paid shape) | Cheap validation, small inference, Whisper/OCR |
| **TPU v5e-1 / v6e-1** | `--tpu V5E1` / `--tpu V6E1` | `~1.7–3.0+` | JAX/Flax, PyTorch/XLA high-throughput batch & simulation |
| **L4** | `--gpu L4` | `~1.5–4.8` | High price/perf workhorse: LoRA, batch embeddings, vLLM medium models |
| **G4** | `--gpu G4` | `~8.9` | Mid-high GPU compute when L4 VRAM is insufficient |
| **A100** | `--gpu A100` | `~5.3–10.6` | Heavy fine-tuning, 40–80 GB VRAM workloads |
| **H100** | `--gpu H100` | `~18` | Peak throughput; **only** after full smoke verification on T4/L4 |

*Note*: `--high-mem` requests High-RAM shapes on eligible plans (ignored for single-shape accelerators like L4 and TPUs) and slightly increases CU/hr.

---

## 3. `google-colab-cli` Command Reference & Patterns

### 3.1 Pre-Run & Post-Run Audit Commands

```bash
colab usage               # Inspect current CU balance and burn rate
colab sessions            # List all active remote sessions
colab status -s <name>    # Inspect hardware, machine shape, and status
colab stop -s <name>      # Terminate VM and stop CU billing immediately
```

### 3.2 Pattern A: Ephemeral Single-Script Job (`colab run` — Default Choice)

`colab run` provisions a fresh VM, runs the local Python script with forwarded arguments, and tears down the VM automatically on exit (unless `--keep` is passed).

```bash
colab run \
  --gpu L4 \
  --timeout 0 \
  --env HF_HUB_DISABLE_PROGRESS_BARS=1 \
  ./train_job.py --drive-root colab --dataset datasets/corpus.tar.gz --run-id run_001
```

**Critical Pitfalls**:
1. **Silent Timeout Trap**: `--timeout` defaults to **`30.0` seconds** without output! Long downloads, compilation, or quiet epochs will abort the client connection unless you pass `--timeout 0` (no idle-output limit) AND print periodic heartbeats with `flush=True`.
2. **Never Pass `--keep` in Automated Agent Runs**: `--keep` leaves the GPU running after the script finishes, silently draining CCUs.
3. **Allocation Retry Env Vars**: For congested GPU pools (e.g., A100/H100), tune:
   - `COLAB_CLI_REQUEST_TIMEOUT=180`
   - `COLAB_CLI_ASSIGN_RETRIES=5`
   - `COLAB_CLI_RETRY_BACKOFF=15`

### 3.3 Pattern B: Multi-Step Stateful Session (`colab new` + `trap`)

Use only when you must install complex requirements, upload multiple files, and run several commands on the same VM:

```bash
set -euo pipefail
SESSION="exp-$(date +%Y%m%d-%H%M%S)"
cleanup() { colab stop -s "$SESSION" >/dev/null 2>&1 || true; }
trap cleanup EXIT

colab new -s "$SESSION" --gpu L4
colab install -s "$SESSION" -r requirements.txt
colab upload -s "$SESSION" ./bundle.tar.gz /content/bundle.tar.gz
colab exec -s "$SESSION" -f ./run_pipeline.py --timeout 0
colab download -s "$SESSION" /content/work/metrics.json ./outputs/metrics.json
colab log -s "$SESSION" -o ./outputs/session_log.ipynb
```

### 3.4 Headless Agent Prohibitions

Never execute interactive TTY commands inside headless agent tool calls:
- `colab repl` (blocks waiting for stdin)
- `colab ssh` without a non-interactive `-- <CMD>` (opens interactive shell)
- `colab edit` (launches `$EDITOR`)
- Interactive `colab drivemount` if it prompts for manual OAuth consent (prefer `rclone` inside the script or browser-authorized `colab-mcp`)

---

## 4. `colab-mcp` Operational Workflow

`colab-mcp` bridges a local Antigravity session to a live Colab notebook in the user's browser over a local WebSocket proxy.

### Available Tools (v1.0.x)

1. `open_colab_browser_connection` — Launches a Colab scratch notebook URL with `#mcpProxyToken=...&mcpProxyPort=...` and waits up to **60 seconds** for the user to approve the connection in the browser. Unlocks the notebook tools below once connected.
2. `get_cells` — Reads cells (`cellIndexStart`, `cellIndexEnd`, `includeOutputs`).
3. `add_code_cell` — Inserts a code cell (`python` / `r` / `julia`) at `cellIndex`; returns `cellId`.
4. `add_text_cell` — Inserts a Markdown cell at `cellIndex`.
5. `update_cell` — Overwrites an existing cell by `cellId`.
6. `move_cell` — Moves a cell to a new `cellIndex`.
7. `delete_cell` — Deletes a cell by `cellId`.
8. `run_code_cell` — Executes a code cell by `cellId` and returns output.

### Mandatory `colab-mcp` Execution Protocol

1. **Connect**: Call `open_colab_browser_connection` and inform the user to confirm in their browser (making sure the browser is signed into the intended Google account).
2. **Verify Runtime**: Run a quick cell checking hardware (`!nvidia-smi` or `import jax; print(jax.devices())`) and free disk (`!df -h /content`).
3. **Mount & Stage**: Mount Drive (`drive.mount('/content/drive')`) or pull via `rclone`, then copy data to `/content/work`.
4. **Run & Persist**: Execute workload cells, writing final outputs/checkpoints back to Drive.
5. **Mandatory Teardown Cell**: Because `colab-mcp` v1.0.x does **not** expose a dedicated `DeleteRuntime` MCP tool, always execute a final teardown cell (or wrap long cell logic in `try ... finally:`):
   ```python
   from google.colab import runtime
   runtime.unassign()
   ```

---

## 5. Mandatory Cost-Stop Python Pattern

Every long-running Colab script or notebook cell must guarantee teardown even on CUDA OOM or uncaught exceptions:

```python
import traceback


def unassign_runtime() -> None:
    try:
        from google.colab import runtime  # type: ignore
        runtime.unassign()
    except Exception:
        pass


try:
    run_workload()
    sync_outputs_to_drive()
except Exception:
    traceback.print_exc()
    sync_partial_logs_to_drive()
    raise
finally:
    unassign_runtime()
```
