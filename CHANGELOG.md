# Changelog

All notable changes to `agy-create-colab-notebook` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-24

Initial public release of **`agy-create-colab-notebook`**, a portable Antigravity Agent Skill for orchestrating Consumer Google Colab GPU/TPU workloads with `rclone` and Google Drive.

### Added
- **Skill Entrypoint & 6-Phase SOP (`SKILL.md`)**:
  - Compute-pool routing gate (Colab GPU/TPU vs local/AGY Swarm vs Gemini Batch API vs ToS-prohibited workloads).
  - Strict Consumer Colab (`colaboratory.googleapis.com`) vs GCP Colab Enterprise / Vertex AI (`aiplatform.googleapis.com`) billing isolation rules.
  - Channel selection matrix covering `google-colab-cli` headless execution and `colab-mcp` browser-bridged notebook collaboration.
  - End-to-end Phase 0–5 workflow: preflight check, routing & compliance gate, `rclone`/Drive staging, zero-error smoke gate, remote execution with local `/content/work` staging, and artifact verification + teardown audit.
- **Progressive Reference Manuals (`references/`)**:
  - `references/setup-and-isolation.md`: macOS/Linux tool installation, BYOC Google Cloud Desktop OAuth setup for `rclone`, `colab-mcp` configuration, and multi-client / multi-CLI isolated `$HOME` & `--app_data_dir` handling.
  - `references/colab-cli-and-mcp.md`: `google-colab-cli` (`colab run --timeout 0`, `colab new/exec` with `trap`, allocation retry env vars) and `colab-mcp` v1.0.x tool reference, tier comparison, approximate `CU/hr` burn rate table, and mandatory `try/finally: runtime.unassign()` pattern.
  - `references/rclone-drive-pipeline.md`: `<drive_root>/{notebooks,datasets,runs,cache}` topology, Drive FUSE vs `/content/work` staging architecture, `750 GB / 24h` safe transfer modes (`--max-transfer 740G` and `--bwlimit 8.5M`), headless VM `rclone` staging, `rclone crypt`, and desktop SSD TBW protection rules.
- **Zero-Dependency Helper Scripts (`scripts/`)**:
  - `scripts/preflight_check.py`: Cross-platform diagnostic tool that detects isolated `$HOME` profiles, augments common `PATH` entries, locates `rclone` and `colab-cli` configs without exposing secrets, and optionally queries live `colab sessions` and `colab usage` (`--check-colab-live`, `--json`).
  - `scripts/rclone_stage.py`: Safe `rclone` wrapper (`init`, `push`, `pull`, `verify`, `ls`) enforcing `740G` daily batch cap or `8.5M` steady-state throttling, optional `/tmp` tar packing (`--pack-tar`), and `--dry-run` support.
- **Reusable Templates (`resources/`)**:
  - `resources/colab_job_template.py`: Runnable Colab job template featuring Drive mount and headless `RCLONE_CONF_B64` staging (mode-0600 randomized temp config), safe Tar Slip–protected local extraction to `/content/work`, flushed timestamped logging, `manifest.json` & `error.log` sync back to Drive, and mandatory `try/finally: runtime.unassign()` teardown.
  - `resources/mcp_config.example.json`: Minimal portable `colab-mcp` MCP server config snippet.
- **Repository Governance**:
  - Added `README.md`, `CHANGELOG.md`, MIT `LICENSE`, and `.gitignore`.
