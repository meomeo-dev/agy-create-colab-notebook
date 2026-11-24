# Setup, Authentication & Multi-Profile Isolation Guide

This guide helps any user (macOS or Linux) install the required tools, configure credentials cleanly, and operate safely when running multiple Antigravity clients or CLIs with isolated data directories.

---

## 1. Prerequisites & Tool Installation

### Required & Optional Tools

| Tool | Purpose | Installation | Platform Notes |
| :--- | :--- | :--- | :--- |
| `uv` | Fast Python tool installer | `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` | macOS & Linux |
| `google-colab-cli` (`colab`) | Headless Colab VM provisioning & execution | `uv tool install google-colab-cli` (Python >= 3.12) | macOS & Linux (Windows via WSL2) |
| `colab-mcp` | Browser-bridged MCP server for Colab notebooks | `uv tool install colab-mcp` (Python >= 3.13) or `uvx git+https://github.com/googlecolab/colab-mcp` | macOS, Linux, Windows |
| `rclone` | High-throughput, throttled, encrypted Drive sync | `brew install rclone` or `sudo -v ; curl https://rclone.org/install.sh \| sudo bash` | macOS & Linux |

Verify installations in a normal terminal:

```bash
which colab colab-mcp rclone uv
colab version
rclone --version | head -n 2
```

---

## 2. Authentication Setup

### 2.1 `google-colab-cli` Authentication

- **Interactive OAuth2 (default)**: Run `colab usage` once in your terminal. It opens a browser OAuth flow and stores the refresh token in the OS keyring (`macOS Keychain` / Linux `SecretService`).
- **Headless / Locked Keyring Fallback**: Pass `--auth adc` after authenticating via `gcloud auth application-default login` with appropriate scopes, or unlock your keyring first.
- **Session Metadata File**: Stored by default at `~/.config/colab-cli/sessions.json` (override with `colab --config /path/to/sessions.json ...`).

### 2.2 `colab-mcp` Configuration in Antigravity

Add an entry to your global `~/.gemini/config/mcp_config.json` (see [../resources/mcp_config.example.json](../resources/mcp_config.example.json)):

```json
{
  "mcpServers": {
    "colab-mcp": {
      "command": "colab-mcp"
    }
  }
}
```

- **Multi-Account Browser Tip**: `colab-mcp` opens Colab in the system default browser using Python's `webbrowser` module. If your paid Colab Google account is signed into a different browser (or browser profile) than your default browser, set the `BROWSER` env var in the `colab-mcp` MCP entry (for example on macOS: `"BROWSER": "/usr/bin/open -a Firefox %s"`).
- **Non-Login Shell PATH Tip**: Desktop apps often launch MCP servers without `~/.local/bin` in `PATH`. If `command: "colab-mcp"` fails to start, use the full path (e.g., `/path/to/.local/bin/colab-mcp`) or `"command": "uvx", "args": ["git+https://github.com/googlecolab/colab-mcp"]`.

### 2.3 `rclone` + Google Drive BYOC OAuth Setup

Never rely on `rclone`'s built-in shared public `client_id` for large transfers — it is heavily rate-limited across all global users.

1. In Google Cloud Console, create a personal project -> enable **Google Drive API**.
2. Configure OAuth Consent Screen (**External** -> add your Google account as a Test User, or Internal for Workspace).
3. Create **OAuth Client ID** of type **Desktop app** -> copy `client_id` and `client_secret`.
4. Create an isolated config file outside any Git repository (for example `~/.config/rclone/gdrive-colab.conf`):
   ```bash
   rclone config --config ~/.config/rclone/gdrive-colab.conf
   ```
   Choose `n` (new remote) -> name (e.g., `gdrive`) -> type `drive` -> paste your own `client_id` & `client_secret` -> scope `drive` (or `drive.file` for stricter scoping) -> complete browser OAuth.
5. Verify without exposing secrets:
   ```bash
   rclone listremotes --config ~/.config/rclone/gdrive-colab.conf
   rclone about gdrive: --config ~/.config/rclone/gdrive-colab.conf
   ```

---

## 3. Multi-Client & Multi-CLI Data Directory Isolation

Many power users run multiple Antigravity accounts simultaneously (e.g., personal vs Pro/Ultra accounts) across Antigravity Desktop App, Antigravity IDE, and Antigravity CLI (`agy`). Understanding how isolation affects **skills**, **PATH**, and **tool configs** prevents subtle failures.

### 3.1 How Isolation Patterns Work

| Surface | Typical Isolation Mechanism | Impact on Skills & External CLIs |
| :--- | :--- | :--- |
| **Antigravity Desktop App (multi-profile)** | Launcher overrides `HOME=<profile_dir>` and `--user-data-dir=<app_support_dir>` | `<profile_dir>/.gemini/config` is usually symlinked to shared `~/.gemini/config`. **Gotcha**: subprocesses inherit overridden `$HOME`, so `rclone` and `colab` look in `<profile_dir>/.config/` instead of the real OS user's `~/.config/`! |
| **Antigravity CLI (`agy`) multi-account** | Wrapper sets `--app_data_dir=<custom_dir>` (and optionally `SSH_CONNECTION=1` to bypass OS keychain sharing) | Shares global `~/.gemini/config/` (rules, MCP, skills). If your CLI setup also maintains per-profile `<app_data_dir>/skills`, symlink the skill or directory so all CLI accounts stay in sync. |
| **Antigravity IDE / Default App** | Uses real OS `$HOME` | Reads `~/.gemini/config/skills/` and real `~/.config/{rclone,colab-cli}/` directly. |

### 3.2 Handling Overridden `$HOME` for `rclone` and `colab`

When running inside an isolated Desktop App profile where `$HOME` is NOT the real OS user home directory:

1. **Recommended — Explicit Environment Variables / Flags (Zero Side Effects)**:
   - Export or pass `--rclone-config` / `RCLONE_CONFIG` pointing to the intended rclone config file.
   - Pass `colab --config <path/to/sessions.json>` when you want session state persisted in a specific location.
   - Set optional defaults recognized by this skill's helper scripts:
     - `COLAB_RCLONE_CONFIG`: path to your rclone config file (fallback: `RCLONE_CONFIG`, then `$HOME/.config/rclone/rclone.conf`, then real OS user `~/.config/rclone/`).
     - `COLAB_RCLONE_REMOTE`: default rclone remote name (e.g., `gdrive`).
     - `COLAB_DRIVE_ROOT`: default Drive workspace folder (default: `colab`).
     - `COLAB_CLI_CONFIG`: path to `colab` `sessions.json` (fallback: `$HOME/.config/colab-cli/sessions.json`).
2. **Optional — Intentional Config Symlinking per Profile**:
   - If a specific isolated profile should share `rclone` or `colab-cli` state with your OS user home, symlink `<profile_home>/.config/rclone` or `<profile_home>/.config/colab-cli` intentionally.
   - **Account Hygiene Warning**: If Profile A and Profile B belong to *different* Google accounts, keep their `rclone` configs (`--config`) and `colab` credentials **separate** so you do not accidentally burn Account A's CCUs or write to Account B's Drive.
3. **Non-Login Shell `PATH` Gap**:
   - GUI launchers often omit `/opt/homebrew/bin`, `/usr/local/bin`, and `<real_home>/.local/bin`. Both `scripts/preflight_check.py` and `scripts/rclone_stage.py` automatically search those standard binary locations.

---

## 4. Installing & Distributing This Skill Repository

This repository is self-contained and holds zero user-specific credentials or hardcoded paths.

### Option A: Standard `git clone` into the Host Skill Directory

Clone the repository directly into your Antigravity user-level or project-level skills directory, keeping the folder name `agy-create-colab-notebook`:

```bash
# Global (Antigravity App / IDE / CLI)
git clone https://github.com/meomeo-dev/agy-create-colab-notebook.git \
  ~/.gemini/config/skills/agy-create-colab-notebook

# Project-scoped (committed or local workspace)
git clone https://github.com/meomeo-dev/agy-create-colab-notebook.git \
  .agents/skills/agy-create-colab-notebook
```

If you maintain a separate CLI skills directory (for example `~/.gemini/antigravity-cli/skills/`), symlink it to the same clone instead of duplicating files:

```bash
ln -sfn ~/.gemini/config/skills/agy-create-colab-notebook \
  ~/.gemini/antigravity-cli/skills/agy-create-colab-notebook
```

### Option B: Shared Team Directory via `skills.json`

Register a shared checkout (or submodule) in `.agents/skills.json` at your project root:

```json
{
  "entries": [
    { "path": "path/to/skills" }
  ]
}
```

Any teammate opening the repository in Antigravity will automatically discover the skill.
