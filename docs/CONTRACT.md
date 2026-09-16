# corral 对外契约

- 契约版本：`1`

对外只有三样：`corral` 命令、它的 JSON 输出、它的退出码。`corral --version` 输出 `{"ok": true, "version": "…", "contract": "1"}`。

**调用方不许读 corral 的内部文件**（状态目录下的任何东西），内部格式随时可改。本文没写的字段、行为都不算契约。

## 通用规则

- **输出**：除 `read`（输出文字）、`attach`（接管终端）、`guide`（输出使用说明）外，每个命令在标准输出写**一行 JSON**（`install-skills` 在终端里确认时，提示写到标准错误）。
- **成功**：`{"ok": true, …}`，退出码 0。
- **失败**：`{"ok": false, "error": "<标识>", "message": "<给人看的说明>", …附加字段}`，以对应退出码退出。附加字段视情况带 `name`、`instance`、`state`、`exited`、`last_human_input`、`proto` 等。
- **沙箱**：除 `guide` 和 `--version` 外，所有命令发现自己在 Codex 沙箱里（环境变量 `CODEX_SANDBOX` 存在），立即以退出码 6 拒绝，不碰状态目录。沙箱里既起不了栏位，也连不上正在跑的栏位。
- **名字**：用 `/` 分段；每段由字母、数字、`.`、`_`、`-` 组成，以字母或数字开头，不能是内部文件名（`lock`、`meta.json`、`sock`、`hook.py`、`events`、`cursor`、`exit.json`、`pen.log`）。名字对应的 socket 路径超过系统限制（macOS 104 字节、Linux 108 字节）时，以 `path_too_long` 拒绝。
- **状态目录**：默认 `~/.corral`，可用环境变量 `CORRAL_HOME` 改；同一用户、同一台机器上的调用方看到的是同一个目录。agent 的环境里带着 `CORRAL_HOME`、`CORRAL_NAME`、`CORRAL_INSTANCE`，agent 里再调用 `corral` 看到的是同一个目录。
- **时间**：所有时间字段都是 Unix 时间戳（秒，浮点数）；`idle_for` 是秒数。

## 取值

- 状态值：`starting` `idle` `working` `blocked` `exiting` `unknown`
  - `starting`：还没收到会话开始事件（启动中，或卡在信任框等对话框里；Codex 在第一次提交输入前一直是这个状态）；或者用 `--prompt` 启动、首句还没提交。
  - `idle`：这一轮结束。**只表示这一轮结束，不代表 agent 不会再动**：agent 可能自己开新一轮（例如后台命令结束后自己注入一条输入），看 `last_input_source`。
  - `working`：收到输入、正在调用工具。
  - `blocked`：弹出了权限框或提问框，等人处理。
  - `exiting`：agent 报告会话结束，正在退出。
  - `unknown`：不认识的 agent（不是 Claude Code、Codex），没有钩子，状态无从得知。
- wait 结果值：`idle` `blocked` `stopped-quiet` `unknown`
  - `stopped-quiet`：只在状态是 `working` 时、用了 `--quiet N` 才会出现：连续 N 秒既没有输出也没有事件。常见原因是人在接入窗口里按 Esc 打断了 Claude Code（它打断时没有事件）。和真正的回合结束（`idle`）是两回事。
- 输入来源值：`send` `human` `agent`（最近一次输入是 corral send 送的 / 接入窗口里的人打的 / agent 自己注入的）
- 按键名：`enter` `esc` `tab` `backspace` `space` `up` `down` `left` `right` `ctrl-c` `ctrl-d`；另外写成 text:<文字> 时原样送出这段文字（不带回车）。

## 命令

### `corral start`

- 用法：`corral start <名字> [--cwd 目录] [--unique] [--prompt 首句] [--env KEY=VALUE]… -- <agent 命令…>`
- 选项：`--cwd` `--unique` `--prompt` `--env`
- 输出字段：`ok` `name` `instance` `kind` `warnings`
- 拉起 agent，返回名字和实例编号（12 位十六进制）。同名 agent 已在跑时退出码 5。
- `--unique`：把名字当前缀，自动补不重复的后缀，如 `demo/ask-3`；以输出里的 `name` 为准。
- `--prompt`：第一句话，作为启动参数交给 agent，由 agent 自己提交。**新开的 agent 送第一句话只能用它**（Codex 在第一次提交前没有任何事件，`send` 会被拒绝）。只支持 Claude Code 和 Codex。
- `--env KEY=VALUE`：给 agent 补充或覆盖环境变量（`CORRAL_*` 盖不掉）。
- `kind`：`claude`、`codex`，或不认识时的程序名。
- 环境：agent 的环境取自用户的登录 shell（和用户新开终端时一致），调用方自己的环境不会传下去。取不到时退回最小环境，`warnings` 里说明。
- 钩子：Claude Code 用 `--settings`、Codex 用 `-c` 加钩子，只对这一个 agent 生效，不写任何全局配置。Codex 同时带上 `--dangerously-bypass-hook-trust`：否则 Codex 启动时会要求人工审核这些钩子，点「信任」会把钩子写进用户的全局配置。
- 钩子由 `/usr/bin/python3` 运行；它不可用时以 `hook_python_unavailable` 拒绝。
- 其他失败标识：`bad_cwd`、`exec_failed`（命令不存在等）、`pen_failed`。

### `corral send`

- 用法：`corral send <名字> <文字> [--force] [--timeout 秒]`
- 选项：`--force` `--timeout`
- 输出字段：`ok` `name` `instance` `confirmed` `merged_with_draft` `latency`
- 送一段话（可以多行）并按回车，**以 agent 的输入事件里的文字和送出的一致为送达确认**，`confirmed: true`。`--timeout` 默认 15 秒。
- 输入框里原有没提交的文字（人留下的草稿）时，送出的文字会和它拼在一起提交：只要送出之后的最近一条输入事件里**包含**送出的文字，也算送达，`merged_with_draft: true`（正常送达时为 `false`）。agent 收到的是拼接后的内容，调用方如果在意，让人接入去看；**不要重送**。
- 拒绝：状态不是 `idle` → 退出码 7（附 `state`）；最近 30 秒内有人在接入窗口里操作过（按键、粘贴、鼠标点击 / 拖动 / 滚轮；终端自动发回的应答和鼠标只是移动不算）→ 退出码 8（附 `last_human_input`），稍后重试或加 `--force`。只是开着窗口看、没操作，照常送。
- 超时没确认 → 退出码 3。**corral 不补发任何按键**：此刻屏幕上可能是菜单或对话框，补发的回车可能被当成选择。
- 不认识的 agent：照样写入并回车，`confirmed: false`，不检查状态。

### `corral keys`

- 用法：`corral keys <名字> <按键>…`
- 选项：
- 输出字段：`ok` `name`
- 送原始按键（见「按键名」），用于回答对话框、打断。**不看状态，后果由调用方负责。** 不认识的按键名是用法错误。

### `corral status`

- 用法：`corral status <名字>`
- 选项：
- 输出字段：`ok` `name` `instance` `kind` `proto` `state` `last_tool` `turn_started` `last_event` `last_event_at` `last_input_at` `last_input_source` `title` `last_output` `idle_for` `attached` `last_human_input` `started`
- `last_tool`：这一轮最近调用的工具名。`turn_started`：这一轮开始的时间。`last_event`：最近一个钩子事件名（只作参考，取值随 agent 版本变化）。
- `title`：agent 设置的终端标题。`last_output`、`idle_for`：最后一次输出的时间、距今秒数。
- `attached`：接入窗口数。`last_human_input`：接入窗口里最后一次人工操作的时间。`proto`：栏位协议版本。
- 不存在 → 退出码 2，附 `exited`（agent 退出过时为 `{"instance", "code", "t", "stop_step"}`，否则 null）。

### `corral wait`

- 用法：`corral wait <名字> [--timeout 秒] [--quiet 秒]`
- 选项：`--timeout` `--quiet`
- 输出字段：`ok` `name` `instance` `kind` `proto` `state` `last_tool` `turn_started` `last_event` `last_event_at` `last_input_at` `last_input_source` `title` `last_output` `idle_for` `attached` `last_human_input` `started` `result`
- 等到这一轮结束：状态稳定在 `idle` 或 `blocked` 约 0.5 秒才返回（避免会话开始和输入事件先后到达时的瞬间误判）。输出 `status` 的全部字段加 `result`。
- `--timeout` 默认 600 秒，超时退出码 4，附当前状态字段；状态是 `starting` 时说明启动没完成（可能卡在对话框里，接入去看）。
- `--quiet N`：见「wait 结果值」里的 `stopped-quiet`。N 由调用方给，没有默认值。`blocked` 由事件先判出，不受影响。
- 不认识的 agent：立即返回 `result: unknown`。

### `corral reply`

- 用法：`corral reply <名字>`
- 选项：
- 输出字段：`ok` `name` `instance` `text` `at`
- 主会话上一轮的最后一条回复原文（多段文字、代码块原样保留），`at` 是那一轮结束的时间。还没有回复时以 `no_reply` 失败（退出码 1）。

### `corral where`

- 用法：`corral where <名字>`
- 选项：
- 输出字段：`ok` `name` `instance` `kind` `cwd` `agent_pid` `started`

### `corral ls`

- 用法：`corral ls`
- 选项：
- 输出字段：`ok` `agents`
- 每项字段：`name` `instance` `kind` `cwd` `started` `starting` `incompatible` `proto`
- 列出所有活着的 agent，顺手清掉栏位已死的残留。正在启动的项只有 `name` 和 `starting: true`；协议版本不兼容的项只有 `name`、`incompatible: true`、`proto`。

### `corral read`

- 用法：`corral read <名字> [--bytes N]`
- 选项：`--bytes`
- 最近的原始输出去掉控制字符后的文字（默认最后 16000 字节）。没有终端模拟，agent 重绘时文字会乱，**只作排查用**，不要解析。

### `corral attach`

- 用法：`corral attach <名字> [--wait]`
- 选项：`--wait`
- 把当前终端接到 agent 上。按 **Ctrl-]** 退出接入，agent 继续跑。第一个接入的窗口能打字，之后的只读；伪终端尺寸跟着能打字的窗口走。
- 退出码：按 Ctrl-] 或终端关闭 → 0；agent 退出 → 2。接管终端之前的错误照常输出 JSON（不存在 → 2；标准输入输出不是终端 → `not_a_tty`）。
- `--wait`：名字还不存在时显示「waiting for <名字>」，一出现就接上；agent 退出后回到等待；按 Ctrl-] 或在等待时按 Ctrl-C 退出，退出码 0。

### `corral stop`

- 用法：`corral stop <名字> [--timeout 秒]`
- 选项：`--timeout`
- 输出字段：`ok` `name` `instance` `exit_code` `stopped_by`
- 先用这种 agent 自己的退出方式，不行再依次升级到信号，最后总是 SIGKILL；**等到 agent 真正退出、名字可以立刻重新 start 才返回**。`stopped_by` 是最后执行到的一步：`keys`、`SIGHUP`、`SIGTERM`、`SIGKILL`。`exit_code` 为负数表示被信号结束。
- 各种 agent 的顺序：Claude Code：SIGHUP → SIGTERM；Codex：连按两次 Ctrl-C，等它收尾（最多 60 秒；收尾时间随会话内容增长，实测跑过几轮后要近 30 秒，期间没有输出）→ SIGTERM（Codex 不理 SIGHUP）；不认识的：SIGHUP → SIGTERM。
- `--timeout` 默认 90 秒（盖住最长的退出序列），超时退出码 4（栏位仍会继续升级到 SIGKILL）。
- agent 自己脱离出去的后台进程（不在它的进程组里）不清。

### `corral install-skills`

- 用法：`corral install-skills [--target all|claude|codex] [--project 目录] [--remove] [--dry-run] [--yes]`
- 选项：`--target` `--project` `--remove` `--dry-run` `--yes`
- 输出字段：`ok` `action` `dry_run` `written` `items` `warnings`
- 每项字段：`agent` `path` `status`
- 把 corral 的 agent skill 写进 `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/corral/SKILL.md`（Claude Code）和 `~/.agents/skills/corral/SKILL.md`（Codex，官方文档的用户级位置），只写这两个文件。**写的是用户的全局目录，必须经人同意**：在终端里运行时先列出每个路径和状态再问 `[y/N]`；不在终端里运行时，没有 `--yes` 就以 `confirmation_required` 拒绝（附 `items`）。回答不是 y 时以 `declined` 失败，什么都不写。
- `status`：安装时 `create` / `same`（内容相同，跳过）/ `overwrite`；`--remove` 时 `remove` / `absent` / `foreign`（不是 corral 写的文件，不删）。
- `--project 目录`：改为写项目级位置 `<目录>/.claude/skills/corral/SKILL.md`（Claude Code）和 `<目录>/.agents/skills/corral/SKILL.md`（Codex），只在 agent 以该目录为工作目录时生效；目录必须已存在，否则 `bad_project`。确认规则相同。
- `--dry-run` 只列出，不写。`--remove` 只删带 corral 标记的 SKILL.md 和变空的 `corral` 目录。PATH 上找不到 `corral` 时 `warnings` 里提示（skill 会让 agent 报告 corral 没装）。
- 在沙箱里拒绝（退出码 6）。

### `corral guide`

- 用法：`corral guide`
- 选项：
- 打印给 agent 看的使用说明。在沙箱里也可用。

## 退出码

| 码 | 标识 | 含义 |
|---|---|---|
| 0 | `ok` | 成功（`wait` 返回 idle / blocked / stopped-quiet / unknown 都算） |
| 1 | `error` | 用法错误或内部错误（细分标识见 JSON 的 `error` 字段，如 `usage`、`bad_name`、`no_reply`） |
| 2 | `not_found` | 不存在或已退出（附退出信息） |
| 3 | `not_delivered` | 送了但没确认送达 |
| 4 | `timeout` | 超时 |
| 5 | `exists` | 同名 agent 已在跑 |
| 6 | `sandbox` | 在沙箱里，拒绝 |
| 7 | `not_idle` | 不是 idle，拒绝 |
| 8 | `human_active` | 最近 30 秒内有人在接入窗口操作过，拒绝 |
| 9 | `incompatible` | 栏位协议或事件格式版本不兼容 |

## 兼容

- 栏位启动后不升级；钩子脚本在启动时复制了一份。所以新版 `corral` 会遇到旧版栏位和旧版钩子写的事件。
- **新版命令至少兼容上一个栏位协议版本和上一个事件格式版本**；遇到更旧或不认识的版本，以退出码 9 拒绝并说明，处理办法是用对应版本的 corral `stop` 后重新 `start`。
- 本契约只做加法：输出可能增加字段，调用方应忽略不认识的字段；删改语义要升契约版本。
