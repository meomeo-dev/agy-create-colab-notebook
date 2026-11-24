# agy-create-colab-notebook

一个通用的 Antigravity Agent Skill：规范如何在 Antigravity（桌面客户端、IDE 与 `agy` CLI）中通过官方 Consumer Colab 通道（`google-colab-cli` / `colab-mcp`）配合 `rclone` 与 Google Drive 完成 GPU/TPU 算力外包、数据 staging 与防空烧止血闭环。

入口规范见 [`SKILL.md`](SKILL.md)，操作细则与参考文档在 `references/`，零依赖辅助脚本在 `scripts/`，通用任务与 MCP 模板在 `resources/`。版本变更见 [`CHANGELOG.md`](CHANGELOG.md)。

## 核心能力与安全约束

- **算力路由与 ToS 门禁**：明确哪些任务适合外包给 Colab GPU/TPU（训练/微调、大规模离线 Embedding/OCR/Whisper/评测、CUDA/Triton/vLLM 调试、TPU 仿真），哪些应留给本地/Swarm 或 Batch API，以及绝对禁止的 ToS 红线。
- **计费边界隔离**：强制走官方 Consumer Colab 通道（`colaboratory.googleapis.com`），避免误调独立按 GCP 计费的 Colab Enterprise / Vertex AI API（`aiplatform.googleapis.com`）。
- **双通道标准工序（Phase 0–5）**：覆盖无头自动化的 `google-colab-cli`（默认优先 `colab run --timeout 0` 自动释放）与浏览器交互式 Notebook 的 `colab-mcp`，全链路纳入零报错冒烟门禁与 `try ... finally: runtime.unassign()` 强制止血。
- **Drive FUSE 避坑与 `750 GB/24h` 限流**：通过 `rclone` 与标准云端目录 `<root>/{notebooks,datasets,runs,cache}` 同步数据，先打包并一次性拷入 Colab 本地 `/content/work` 再计算，规避 Drive FUSE 随机小文件 I/O 瓶颈与 750 GB 日上传墙，同时保护桌面端 SSD TBW。
- **多端 / 多 CLI 隔离兼容**：支持 Antigravity 多客户端隔离 `$HOME` 与 `--app_data_dir` 多账号场景，自动探测真实用户主目录、常见 `PATH` 与 `rclone`/`colab-cli` 配置路径。

## 运行要求

- **订阅通用**：兼容 Colab Free、Pay-As-You-Go、Google AI Pro 与 Google AI Ultra（5x/20x），不绑定固定套餐或个人目录。
- **系统支持**：macOS 与 Linux（Windows 可通过 WSL2 使用 `google-colab-cli`，或直接使用 `colab-mcp`）。
- **前置工具**：
  - Python 3.9+（辅助脚本仅使用标准库，零额外依赖）
  - [`google-colab-cli`](https://github.com/googlecolab/google-colab-cli)（`uv tool install google-colab-cli`，推荐用于无头批处理）与/或 [`colab-mcp`](https://github.com/googlecolab/colab-mcp)（用于浏览器交互式 Notebook 协作）
  - [`rclone`](https://rclone.org/)（建议配合个人 Google Cloud Desktop BYOC OAuth 凭据）

## 安装

将仓库克隆到宿主的 Skill 目录，目录名保持 `agy-create-colab-notebook`（可固定到指定发布标签，如 `v0.1.0`）：

```sh
git clone --branch v0.1.0 --depth 1 \
  https://github.com/meomeo-dev/agy-create-colab-notebook.git \
  ~/.gemini/config/skills/agy-create-colab-notebook
```

常见目录位置：

| 场景 / 宿主 | 目录路径 |
| :--- | :--- |
| Antigravity（App / IDE / CLI 全局） | `~/.gemini/config/skills/agy-create-colab-notebook` |
| 项目级（团队共享） | `.agents/skills/agy-create-colab-notebook` |
| 多 CLI 独立目录（如按 `--app_data_dir` 单独维护 `skills/`） | 软链至同一份克隆目录即可 |

本地开发或多端共用同一份仓库时，直接建立软链即可：

```sh
ln -sfn /path/to/agy-create-colab-notebook \
  ~/.gemini/config/skills/agy-create-colab-notebook
```

## 快速体检

```sh
python3 scripts/preflight_check.py --check-colab-live
python3 scripts/rclone_stage.py --help
```

在隔离 `$HOME` 的多客户端环境中，脚本支持通过 `--rclone-config`、`RCLONE_CONFIG`、`COLAB_RCLONE_REMOTE`、`COLAB_CLI_CONFIG`、`COLAB_DRIVE_ROOT` 显式覆盖配置。详见 [`references/setup-and-isolation.md`](references/setup-and-isolation.md)。

## 仓库结构

```text
agy-create-colab-notebook/
├── SKILL.md                           # 技能主入口与 Phase 0–5 完整 SOP
├── README.md                          # 仓库说明与安装方式
├── CHANGELOG.md                       # 版本演进记录（Keep a Changelog + SemVer）
├── LICENSE                            # MIT License
├── references/
│   ├── setup-and-isolation.md         # 工具安装、BYOC OAuth 与多端隔离配置
│   ├── colab-cli-and-mcp.md           # google-colab-cli / colab-mcp 手册与 CU 速率参考
│   └── rclone-drive-pipeline.md       # Drive Staging、750 GB/日限流与 SSD TBW 保护
├── scripts/
│   ├── preflight_check.py             # 环境与多端隔离预检脚本
│   └── rclone_stage.py                # 限流安全的 Drive 数据同步封装
└── resources/
    ├── colab_job_template.py          # 含 /content/work staging 与 try/finally 止血的任务模板
    └── mcp_config.example.json        # 通用 colab-mcp 配置示例
```

## 版本

版本遵循语义化版本（[Semantic Versioning](https://semver.org/spec/v2.0.0.html)），每个发布对应一个 `vX.Y.Z` 标签。完整变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

[MIT License](LICENSE)。
