# Installation guide for AI agents

This page is written to be handed to an AI coding agent (Claude Code, Codex, Hermes itself, …) that installs the plugin for you. Paste one of the prompts below into the agent. Humans can follow the same steps by hand; the [README](../README.md#install) has the short version.

- [Quick prompt](#quick-prompt) — install and smoke-test, nothing else.
- [Full prompt](#full-prompt) — prerequisites, install, verification, optional tool-execution audit, optional default/gateway setup, report.
- [Reference](#reference) — what each step does and how to fix failures.
- [中文 prompt](#中文-prompt)

Every step below was run for real on Hermes Agent v0.21.5 with `agy` 1.2.11 on Linux.

## Quick prompt

```text
Install the Hermes Agent model-provider plugin "antigravity-agy" (https://github.com/Realtyxxx/hermes-plugin-antigravity-agy) and smoke-test it.

Rules: do not read, print or copy any credential file (in particular nothing under ~/.gemini/antigravity-cli/ that holds tokens); do not change ~/.hermes/config.yaml; do not restart hermes-gateway. Stop and ask me if a step fails for a reason not covered here.

1. Check prerequisites: `hermes --version` must be >= 0.21.4; `agy --version` must succeed; `agy -p=/quota` must print quota rows (it spends no quota). If agy is missing or not signed in, stop and tell me: I must install agy from https://antigravity.google/docs/cli/install and run `agy` once interactively to sign in.
2. Install: `hermes plugins install Realtyxxx/hermes-plugin-antigravity-agy --no-enable` (model-provider plugins work without enabling; `--no-enable` avoids an interactive prompt). If it is already installed, add `--force` to update it.
3. Validate: `hermes plugins validate "${HERMES_HOME:-$HOME/.hermes}/plugins/antigravity-agy"` must end with "Validation passed."
4. Smoke test: `hermes chat -q "Reply with exactly: INSTALL-OK" --provider antigravity-agy --model gemini-3.8-flash-low -Q` must print INSTALL-OK.
5. Report the Hermes and agy versions and the output of steps 3 and 4.
```

## Full prompt

```text
You are installing the Hermes Agent model-provider plugin "antigravity-agy" from https://github.com/Realtyxxx/hermes-plugin-antigravity-agy for me. It lets Hermes use my Google Antigravity subscription through the official, signed-in `agy` CLI; agy acts only as the model and Hermes executes every tool.

Hard rules (never break them, even to fix an error):
- Never read, print, copy, move or link credential files. In particular leave everything under ~/.gemini/antigravity-cli/ alone except running the `agy` binary itself, and do not open ~/.hermes/.env or ~/.hermes/auth.json.
- Do not modify ~/.hermes/config.yaml unless I explicitly ask in step 6. Do not restart, stop or signal hermes-gateway unless I explicitly confirm in step 6.
- Do not send messages on any messaging platform (Telegram, Discord, …).
- Use `gemini-3.8-flash-low` for test calls and keep them to the minimum below (each real call spends a little of my subscription quota).
- If HERMES_HOME is set, it is the Hermes home; otherwise it is ~/.hermes. Use "$H" below for it.
- Stop and ask me whenever a step fails for a reason not listed in the troubleshooting notes.

Step 1 — prerequisites (read-only):
- `hermes --version` → must be >= 0.21.4. If older, stop and tell me to run `hermes update`.
- `agy --version` → must succeed. If `agy` is not found, stop: I need to install it from https://antigravity.google/docs/cli/install (or tell you the binary path; then export ANTIGRAVITY_AGY_COMMAND=<path> for the commands below).
- `agy -p=/quota` → must print tab-separated quota rows such as "Gemini Models<TAB>Weekly Limit Remaining<TAB>…". It is answered locally and spends no quota. If it reports that I am not signed in, stop: I must run `agy` once interactively as this same OS user to sign in (it needs a browser).
- Note whether `agy` is a wrapper that needs environment variables (for example a proxy launcher that requires HTTPS_PROXY). Any process that runs Hermes with this plugin, including a gateway service, needs the same environment.

Step 2 — install:
- `hermes plugins install Realtyxxx/hermes-plugin-antigravity-agy --no-enable`
  - Model-provider plugins are discovered automatically from "$H/plugins/antigravity-agy/", so enabling is not needed; `--no-enable` avoids the interactive confirmation (in a non-interactive shell `--enable` stops at "dependency install skipped").
  - If the plugin is already installed, re-run with `--force` to update it.
  - The command must report "Plugin installed" and "$H/plugins/antigravity-agy/plugin.yaml" must exist.

Step 3 — validate (no model call):
- `hermes plugins validate "$H/plugins/antigravity-agy"` → every check ✓ and "Validation passed." (the security scan must say "safe").

Step 4 — smoke test (one real call):
- `hermes chat -q "Reply with exactly: INSTALL-OK" --provider antigravity-agy --model gemini-3.8-flash-low -Q`
- Expected: the output contains INSTALL-OK followed by a session_id line. Output piped into `head`/`tail` can show a harmless BrokenPipeError; redirect to a file instead of truncating a pipe.

Step 5 — tool-execution audit (optional but recommended; a few real calls):
- Create a scratch directory with a file notes.txt containing five lines, one of them "the secret word is ORCHID-42". In that directory run:
  `ANTIGRAVITY_AGY_TRACE_DIR=<scratch>/trace hermes chat -q "Use the read_file tool to read notes.txt and find the secret word, then use the terminal tool to run 'wc -l notes.txt'. Reply with the secret word and the line count." --provider antigravity-agy --model gemini-3.8-flash-low --yolo`
  (`--yolo` only skips Hermes' own command-approval prompt for this non-interactive run.)
- Expected: Hermes shows it ran read_file and terminal, and answers ORCHID-42 and 5.
- Then: `python3 "$H/plugins/antigravity-agy/scripts/audit_trace.py" <scratch>/trace` → must end with "violations: 0" (every tool step is a Hermes tool dispatched through call_mcp_tool; agy ran none of its own tools).
- The trace contains conversation content: move the scratch directory to the trash when done (use `trash-put` if available).

Step 6 — only if I ask for it:
- To use it for a session: in an interactive Hermes session run `/model gemini-3.8-flash-medium --provider antigravity-agy` (session only) or pick "Antigravity (agy CLI)" in `/model`.
- To make it my default: back up $H/config.yaml, then set `model.provider: antigravity-agy` and `model.default` to a slug from `agy models` that includes the effort suffix (for example `gemini-3.8-flash-medium`); show me the diff.
- For a running gateway: the gateway only loads new plugin code after a restart. Ask me before restarting it (`systemctl --user reload hermes-gateway` or `hermes gateway restart`). The gateway's environment must satisfy agy's needs from step 1. Idle pre-warmed agy processes use ~416 MB each; on a memory-constrained host suggest ANTIGRAVITY_AGY_PREWARM=1 (or 0) in the gateway environment.

Step 7 — report:
- Hermes and agy versions, the installed plugin version (from plugin.yaml) and commit (`git -C "$H/plugins/antigravity-agy" rev-parse --short HEAD`), results of steps 3–5, anything you changed, and anything that still needs my action.
```

## Reference

### What the install does

`hermes plugins install` clones the repository into a temporary directory, runs Hermes' security scan and publishes it to `$HERMES_HOME/plugins/antigravity-agy/`. On first start Hermes imports it because `plugin.yaml` declares `kind: model-provider`. No configuration is written besides Hermes' own `_config_version` bookkeeping.

To install an exact reviewed commit instead of the branch tip, add `--ref <40-character commit SHA>`.

### Troubleshooting

| Symptom                                                                       | Cause                                                            | Fix                                                                                                          |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `The Antigravity CLI is not installed (no agy on PATH)`                       | `agy` not on the `PATH` of the Hermes process                    | Install agy, or set `ANTIGRAVITY_AGY_COMMAND=/path/to/agy`                                                   |
| `agy is installed but not signed in`                                          | agy has no sign-in for this OS user                              | Run `agy` once interactively as that user                                                                    |
| `agy request failed: invalid model selection … requires --effort`             | A model id agy does not know as given                            | Use a slug from `agy models` (bare families such as `gemini-3.8-flash` are mapped automatically since 0.1.0) |
| `Antigravity subscription quota exhausted or rate limited`                    | 5-hour or weekly quota used up                                   | Check `agy -p=/quota`; wait for the reset or switch model group                                              |
| `agy-env-proxy: no proxy found …` (or another wrapper error, exit code 2)     | `agy` is a wrapper that needs environment variables Hermes lacks | Give the Hermes process (e.g. the gateway unit) the same environment as your shell                           |
| `AgyAgentNotLoaded`                                                           | agy ignored the relay agent and fell back to its default agent   | Update agy and the plugin; the request is refused on purpose so agy's built-in tools never run               |
| `AgyNativeToolViolation`                                                      | The model tried a tool outside Hermes' inventory                 | Retry; if it persists, open an issue with the trace (`ANTIGRAVITY_AGY_TRACE_DIR`)                            |
| `Cannot enable antigravity-agy: dependency install skipped (non-interactive)` | `--enable` in a non-interactive shell                            | Harmless for this plugin; use `--no-enable`                                                                  |
| `ModuleNotFoundError: No module named 'ruamel'` from Hermes itself            | Hermes is mid-update or its environment is incomplete            | Wait for `hermes update` to finish and retry                                                                 |
| New plugin version not used by the gateway                                    | The gateway imports plugin code once                             | Restart the gateway                                                                                          |

### Uninstall

```bash
hermes plugins remove antigravity-agy
```

If you set `model.provider: antigravity-agy` in `config.yaml`, switch it back first.

## 中文 prompt

把下面整段粘贴给 AI 编码助手即可。

```text
请帮我安装 Hermes Agent 的 model-provider 插件 antigravity-agy（https://github.com/Realtyxxx/hermes-plugin-antigravity-agy），并做验证。它让 Hermes 通过本机已登录的官方 agy CLI 使用我的 Google Antigravity 订阅；agy 只当模型，所有工具由 Hermes 执行。

硬性规则（即使为了排错也不能违反）：
- 不读取、打印、复制、移动或链接任何凭证文件；~/.gemini/antigravity-cli/ 下除了运行 agy 本身以外一律不动；不打开 ~/.hermes/.env 和 ~/.hermes/auth.json。
- 除非我在第 6 步明确要求，不修改 ~/.hermes/config.yaml；除非我明确确认，不重启、停止或向 hermes-gateway 发信号。
- 不在任何消息平台（Telegram、Discord 等）发送消息。
- 测试调用只用 gemini-3.8-flash-low，并且只做下面列出的几次（每次真实调用都会消耗少量订阅额度）。
- 如果设置了 HERMES_HOME，它就是 Hermes home，否则是 ~/.hermes，下文记为 $H。
- 任何一步因为下面没列出的原因失败时，停下来问我。

第 1 步：检查前提（只读）
- `hermes --version` 必须 >= 0.21.4，否则停下并提示我运行 `hermes update`。
- `agy --version` 必须成功；找不到 agy 就停下，提示我从 https://antigravity.google/docs/cli/install 安装（或者由我告诉你路径，之后的命令里设置 ANTIGRAVITY_AGY_COMMAND=<路径>）。
- `agy -p=/quota` 必须输出制表符分隔的额度行（本地应答，不消耗额度）。如果提示未登录，停下，提示我以同一个系统用户交互式运行一次 `agy` 完成登录（需要浏览器）。
- 留意 agy 是否是需要环境变量的包装脚本（例如要求 HTTPS_PROXY 的代理启动器）。任何用这个插件运行 Hermes 的进程（包括 gateway 服务）都需要同样的环境。

第 2 步：安装
- `hermes plugins install Realtyxxx/hermes-plugin-antigravity-agy --no-enable`
  - model-provider 插件会从 "$H/plugins/antigravity-agy/" 自动发现，不需要 enable；`--no-enable` 可以避开交互确认（非交互环境下用 `--enable` 会停在 "dependency install skipped"）。
  - 如果已经装过，加 `--force` 更新。
  - 输出应包含 "Plugin installed"，并且 "$H/plugins/antigravity-agy/plugin.yaml" 存在。

第 3 步：校验（不调用模型）
- `hermes plugins validate "$H/plugins/antigravity-agy"`：所有检查打勾，最后是 "Validation passed."，安全扫描为 "safe"。

第 4 步：冒烟测试（1 次真实调用）
- `hermes chat -q "Reply with exactly: INSTALL-OK" --provider antigravity-agy --model gemini-3.8-flash-low -Q`
- 期望输出包含 INSTALL-OK 和一行 session_id。输出接 head/tail 截断管道时可能出现无害的 BrokenPipeError，请改为重定向到文件。

第 5 步：工具执行审计（可选，推荐；几次真实调用）
- 新建临时目录，写入 5 行的 notes.txt，其中一行是 "the secret word is ORCHID-42"，在该目录执行：
  `ANTIGRAVITY_AGY_TRACE_DIR=<临时目录>/trace hermes chat -q "Use the read_file tool to read notes.txt and find the secret word, then use the terminal tool to run 'wc -l notes.txt'. Reply with the secret word and the line count." --provider antigravity-agy --model gemini-3.8-flash-low --yolo`
  （`--yolo` 只是跳过 Hermes 自己的命令审批提示，便于非交互运行。）
- 期望：Hermes 显示执行了 read_file 和 terminal，回答 ORCHID-42 和 5。
- 然后运行 `python3 "$H/plugins/antigravity-agy/scripts/audit_trace.py" <临时目录>/trace`，最后一行必须是 "violations: 0"（所有工具步骤都是经 call_mcp_tool 派发的 Hermes 工具，agy 没有执行任何自带工具）。
- trace 含对话内容，完成后把临时目录移到回收站（有 trash-put 就用 trash-put）。

第 6 步：仅在我要求时执行
- 在某个会话里使用：交互式 Hermes 中执行 `/model gemini-3.8-flash-medium --provider antigravity-agy`（只作用于当前会话），或在 `/model` 选择器里选 “Antigravity (agy CLI)”。
- 设为默认：先备份 $H/config.yaml，再把 `model.provider` 设为 `antigravity-agy`、`model.default` 设为 `agy models` 里带强度后缀的 slug（例如 `gemini-3.8-flash-medium`），并给我看 diff。
- 正在运行的 gateway：只有重启后才会加载新的插件代码。重启前先问我（`systemctl --user reload hermes-gateway` 或 `hermes gateway restart`）。gateway 的环境要满足第 1 步中 agy 的需要。每个空闲的预热 agy 进程约占 416 MB 内存，内存紧张时建议在 gateway 环境中设置 ANTIGRAVITY_AGY_PREWARM=1（或 0）。

第 7 步：汇报
- Hermes 与 agy 的版本、插件版本（plugin.yaml）和提交（`git -C "$H/plugins/antigravity-agy" rev-parse --short HEAD`）、第 3–5 步的结果、你改动了什么，以及还需要我做什么。
```
