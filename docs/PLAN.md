# corral 正式版实现计划

依据：[DESIGN.md](DESIGN.md)（设计）、[SPIKE.md](SPIKE.md)（试验结论）。2026-09-15 人批准。
里程碑做完一个勾一个；改计划先改这里。

## 约束

- 只用 Python 标准库；命令和栏位要求 Python ≥ 3.11；`hook.py` 要求 Python ≥ 3.9（由 `/usr/bin/python3` 运行）。
- macOS 和 Linux；不做 Windows。
- 不安装：只提供 `bin/corral` 和无依赖的 `pyproject.toml`。skill 只能通过 `corral install-skills` 经人确认后写入全局目录，默认不装；M8 前经人同意再装。corral 本身怎么进 PATH，由人决定。
- `spike/` 只作参考，不直接复用；移植试验里验证过的做法。
- 每个行为先写测试并确认失败，再实现（测试用标准库 `unittest`）。
- M0–M7 不调用真实 agent，不花额度；M8 用人的真实配置和便宜模型，需要触发权限框的步骤先问人。

## 目录结构

```
bin/corral                 启动脚本（#!/usr/bin/env python3，把 src 加进路径；版本不够就报错）
pyproject.toml
src/corral/
  cli.py        参数解析、JSON 输出
  errors.py     退出码、错误类型、契约版本号
  paths.py      状态目录（默认 ~/.corral，CORRAL_HOME 可改）、名字校验、socket 路径长度、目录和文件权限
  sandbox.py    沙箱识别（所有命令共用）
  registry.py   扫描状态目录：ls、清残留
  client.py     连接栏位、请求 / 应答、协议版本协商
  protocol.py   握手（一行 JSON）+ 接入后的分帧；协议版本号
  spawn.py      start：加锁、--unique、复制 hook.py、试跑 /usr/bin/python3、两次 fork、等栏位就绪
  env.py        登录 shell 取环境 + 兜底剥会话变量 + --env
  pen.py        栏位事件循环（只 import 标准库，启动时一次性加载完）
  termmodes.py  终端标题 / 终端模式的记录、重放、还原；退出键解析；人工按键和终端自动回应的区分
  attach.py     接入客户端、--wait
  events.py     增量读事件文件（cursor）、只认主会话、状态机、最近一次输入的来源
  hook.py       钩子脚本（Python 3.9 兼容，只用 json/os/sys/time）
  agents/       base.py（钩子命令）、claude.py、codex.py：启动参数（钩子、首句）、正常退出方式
docs/CONTRACT.md      对外契约 v1
src/corral/AGENT_USAGE.md  给 agent 看的使用说明（corral guide 打印；随包分发，所以不放 docs/）
src/corral/skill/SKILL.md   Claude Code 和 Codex 共用的 skill（要点 + 指向 corral guide）
src/corral/skills.py        install-skills：列出路径、确认、写入 / 删除
tests/
  fake_agents/        假 claude（读 --settings 执行钩子）、假 codex（用 tomllib 解析 -c）
  fixtures/           上一个协议版本的栏位实现、各事件格式版本的样例（兼容测试用）
```

## 定下来的实现细节

**退出码（写进契约）**

| 码 | 含义 |
|---|---|
| 0 | 成功（`wait` 返回 idle / blocked / stopped-quiet 都算） |
| 1 | 用法错误或内部错误 |
| 2 | 不存在或已退出（附退出信息） |
| 3 | 送了但没确认送达 |
| 4 | 超时 |
| 5 | 同名 agent 已在跑 |
| 6 | 在沙箱里，拒绝 |
| 7 | 不是 idle，拒绝 |
| 8 | 最近 30 秒内有人在接入窗口按过键，拒绝 |
| 9 | 栏位协议或事件格式版本不兼容（按 DESIGN 5.4 新增） |

**钩子**：`start` 把 `hook.py` 复制到该 agent 的状态目录（0600），钩子命令是 `/usr/bin/python3 <状态目录>/hook.py <事件>`。`start` 先带超时试跑 `/usr/bin/python3 -c 'import json,os,sys,time'`，失败就拒绝。每行事件带格式版本号、实例编号，只写状态计算需要的字段（session_id、cwd、是否有对话记录、工具名、prompt、last_assistant_message、source、notification_type）。

**事件增量读**：`cursor` 文件记实例编号、读到的字节位置、快照（主会话、状态、最近工具、这一轮开始时间、最近几次输入的文字摘要和时间、上一轮回复、暂存的未归属事件）。只消费到最后一个完整换行；实例变了或文件变短就重算；原子替换写回。

**权限**：状态目录各层 0700；lock、meta.json、sock、hook.py、events、cursor、exit.json、pen.log 都是 0600，创建时显式设置，不依赖 umask。

**协议兼容**：栏位在握手应答和 meta.json 里报协议版本；命令支持当前和上一个版本，其他版本退出码 9。`tests/fixtures/` 固定保存上一个协议版本的栏位（v1 发布时先保存 v1 本身，协议升到 v2 时它自然成为「上一个版本」），测试用当前命令去操作它，外加一个「栏位报出不认识的版本」的测试。

**send**：只在 idle 时送；「最近 30 秒有人工按键」由栏位在写入那一刻判断；agent 打开了粘贴模式（2004）时单行也用粘贴模式包起来（M8 验证）；比对前统一换行、去首尾空白；不补发按键。

**人工按键**：焦点事件、光标位置报告、设备属性报告、OSC 回答等终端自动回应不算；鼠标、滚轮算。

**输入来源**：栏位在内存里记最近几次 `send` 的文字摘要和时间；输入事件的文字匹配某次 send → `send`；否则事件前 2 秒内有人工按键 → `human`；否则 → `agent`。

**环境**：用最小环境（HOME、USER、LOGNAME、SHELL、TMPDIR、LANG、SSH_AUTH_SOCK）运行 `$SHELL -l -i -c`，标记包住 `env -0` 的输出只取标记之间；超时 10 秒退回白名单环境并在 start 输出里带警告；兜底剥 `CODEX_*`、`CORRAL_*` 和会话标识变量；`--env` 最后覆盖。

**stop**：栏位执行退出流程（Claude Code：SIGHUP；Codex：自己的退出方式，M8 确定；超时后 SIGTERM、SIGKILL）；命令等到栏位退出才返回。

**wait**：0.2 秒轮询，idle 稳定 0.5 秒才算；`--timeout` 默认 600 秒；`--quiet N` 只在 working 时生效，返回 `stopped-quiet`；starting 时超时，结果标明启动未完成。

**socket 路径**：放在名字目录里，超过 104 字节 `start` 拒绝。

**skill**：一份 SKILL.md 给两家共用。触发描述写中英文说法（「开一个 Claude Code / Codex 看一下」「交给另一个 agent」「delegate to another agent」等）。开头自检 `command -v corral`，找不到就告诉用户没装并停下。标准流程是 `start --unique --prompt` → `wait --timeout 90 --quiet 120`（退出码 4 就重复，因为 agent 自己的 shell 工具有超时）→ `reply` → 用完 `stop`。退出码处理：7、8 稍后重试；6 告诉用户不带沙箱重启调用方（如 `--yolo`）；3 不要重试回车，看状态或让人接入。规矩：不替对方回答对话框；只给自己启动的 agent 送话；名字由调用方起。完整说明运行 `corral guide`。

**install-skills**：目标 `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/corral/SKILL.md` 和 `~/.agents/skills/corral/SKILL.md`（Codex 官方文档位置，M8 后定），只写这两个文件；先列出每个路径的状态（新建 / 相同跳过 / 内容不同将覆盖），终端里问 `[y/N]`；非终端必须显式 `--yes`；`--dry-run` 只列不写；`--target claude|codex|all`；`--remove` 只删带 corral 标记的 SKILL.md 及其空目录；`--project <目录>` 写项目级位置（`<目录>/.claude/skills/`、`<目录>/.agents/skills/`）；沙箱里拒绝；PATH 上没有 corral 时提示。

## 里程碑

- [x] **M0 骨架**：cli、paths、sandbox、退出码；CONTRACT.md 初稿。
  验证：单元测试——名字校验、socket 路径超长拒绝、各命令在沙箱里一律退出码 6、退出码常量和契约一致。
- [x] **M1 栏位 + start / ls / where / read / status（终端层字段）**，被测对象是普通命令（`sh`）。
  验证：集成测试——同名加锁（5）、`--unique`、start 退出后栏位仍在且父进程是 1、agent 退出码记录、手动杀栏位后 ls 清残留；**权限**：状态目录各层 0700，lock / meta.json / sock / exit.json / pen.log 为 0600（hook.py、events、cursor 的权限在 M4 补测）；栏位协议版本出现在握手和 meta.json。
- [x] **M2 环境**。
  验证：单元测试用假 `SHELL` 脚本——配置往外打印、配置慢触发超时退回白名单并带警告、会话变量被剥、`--env` 覆盖；集成测试检查 agent 实际拿到的环境。
- [x] **M3 attach / attach --wait**、终端模式重放和还原、退出键、人工按键时间。
  验证：伪终端测试——尺寸传递和改尺寸、第二个接入者只读、Ctrl-] 的单字节和 CSI u 两种编码、焦点事件 / 光标位置报告不更新人工按键时间而普通按键和鼠标会、`--wait` 两轮自动接上又回到等待。
- [x] **M4 钩子、适配器、事件、状态**。
  验证：hook.py 用 `/usr/bin/python3` 跑通、不往标准输出写、只用允许的模块（测试检查 import）；钩子命令不含仓库路径和 start 时的 Python 路径；hook.py / events / cursor 为 0600；假 agent 测试——主会话过滤（子会话噪声）、会话开始和输入乱序、agent 自己开新一轮、权限请求变 blocked、打断事件；**增量读**：大事件文件只读新增部分（在已读位置之前写入坏数据不影响结果）、半行不消费、实例变化和文件变短重算、两个 status 并发结果一致；**兼容**：事件格式上一个版本可读，不认识的版本退出码 9。
- [x] **M5 send / keys / wait --quiet / reply / start --prompt**。
  验证：假 agent 测试——非 idle 拒绝（7）、30 秒内人工按键拒绝（8）和强制参数、终端自动回应不触发 8、按文字确认送达、确认失败退出码 3 且没有补发按键、输入来源三种、`stopped-quiet` 只在 working 时出现、`--prompt` 由假 agent 自己提交、wait 在「会话开始比输入早几十毫秒」时不提前返回。
- [x] **M6 stop 与协议兼容**。
  验证：假 codex 不理 SIGHUP 时按顺序升级信号；stop 等到栏位退出才返回，之后立刻同名 start 成功；用 `tests/fixtures/` 里上一个协议版本的栏位跑 status / send / attach / stop；栏位报不认识的协议版本时退出码 9。
- [x] **M7 文档**：CONTRACT.md 定稿（命令、JSON 字段、状态值、退出码、兼容规则、`--dangerously-bypass-hook-trust` 的原因）、AGENT_USAGE.md、`corral guide`。
  验证：检查脚本比对 CLI 实际的命令 / 退出码和契约文档一致；按 AGENT_USAGE.md 走一遍委派流程（假 agent）。
- [x] **M7b agent skill 与 install-skills**（先改 DESIGN.md 13.3、14 节，契约加 `install-skills`）。
  验证：skill 内容测试（命令都能解析、退出码与 errors 表一致、提到 `corral guide`、有中英文触发说法、不超过约 80 行、前置信息合法）；install-skills 测试全部在临时目录里跑，用 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 指过去（没有 `--yes` 的非终端环境拒绝写入、`--dry-run` 不写、只写这两个文件、相同内容跳过、覆盖前列出、`--remove` 只删带标记的文件、沙箱里拒绝、PATH 上没有 corral 时给提示）；契约核对脚本覆盖新命令。**这一步不往真实全局目录写任何东西。**
- [x] **M8 真实 agent 冒烟**（人的真实配置、便宜模型；每次前后比对全局配置文件哈希；需要权限框的步骤先问人）。
  验证：经人同意建 `~/.local/bin/corral` 软链让 corral 进 PATH；skill 用 `install-skills --project` 装到测试工作目录（Claude `/tmp/crc`、Codex `/tmp/crx`），不装全局；Codex 若不认项目级 skill，才临时装进全局 Codex skill 目录、测完 `--remove`；实测 Codex 读 `~/.codex/skills` 还是 `~/.agents/skills`；全局安装与否 M8 后由人定；分别对 Claude Code 和 Codex 用自然语言说「开一个 Claude Code 看一下这个想法：…」，确认 skill 自动加载并走完四步（start → wait → reply → stop）、回复逐字对得上（外层 agent 会弹权限框的话先问人）；SPIKE 第 2、3、4、9、12 条再跑一遍；确定 Codex 的正常退出方式；验证单行也走粘贴模式；验证钩子副本在删除 / 移动仓库副本后仍工作（用仓库的临时副本启动）。

## M8 结果（2026-09-15，Claude Code 2.1.272 haiku / sonnet，Codex 0.154 gpt-5.6-luna low，用户真实配置）

每步前后比对 `~/.claude/settings.json`、`~/.codex/config.toml`、`~/.codex/hooks.json` 哈希：唯一变化是经人同意信任 `/tmp/crx` 写入的 `[projects."/private/tmp/crx"]` 两行（已证明去掉这两行后哈希与基线一致；按人的要求不删，只告知）。

**通过**
- 送达与回复：两家多行 + 代码块逐字一致；忙时 send 退出码 7；Codex 单行 `?` 开头送达正确。
- Esc 打断：Claude Code 无事件，`wait --quiet 5` 第 6 秒返回 `stopped-quiet`；Codex 有 `Interrupt` 事件。
- 信任框：Codex 在新目录弹框时状态 `starting`；按回车（经人授权）后 `--prompt` 首句自动提交。
- 接入与断开：两家接入窗口打字（来源 `human`）、SIGKILL 窗口后 agent 仍在、换尺寸重接能看到先前对话、Ctrl-] 退出；退出后立刻 send 按设计 `human_active`，31 秒后送达，对话还在。
- 权限框（人亲手点）：弹框 0.1 s 内 `blocked`，停 29 s 不误判，点允许后 working → idle，文件写成。
- 钩子独立：用仓库临时副本启动 Claude Code，删掉副本后下一轮事件、状态、回复正常。
- skill（项目级安装，未装全局）：Claude Code 从 `<目录>/.claude/skills`、Codex 从 `<目录>/.agents/skills` 加载；外层 **sonnet** 和外层 **Codex luna** 听到「开一个 Claude Code 看一下这个想法…」都加载 corral skill，走完自检 → `start --unique --prompt` → `wait --timeout 90 --quiet 120` → reply → stop，最终回答逐字包含内层回复。外层 **haiku** 看到了 corral skill 但没选：第一次自己作答，改描述后改用内置 Agent 子代理。
- Codex 用户级 skill：`~/.codex/skills` 和 `~/.agents/skills` 两处都会读；人定只装官方文档位置 `~/.agents/skills`。
- 人的决定：skill 暂不装全局；`~/.local/bin/corral` 软链保留；skill 示例不指定模型，补一句「需要省钱时调用方可自己传模型参数」；`/private/tmp/crx` 信任记录已由人删除。

**M8 发现并已修复（都补了测试）**
1. 栏位崩溃杀掉 agent：macOS kqueue 把同一 socket 的可写、可读拆成两条事件，杀掉接入窗口时先写失败断开、再读已关闭的 socket 抛 EBADF。现在处理前先确认连接还在，任何单个连接的意外只断开该连接。
2. `--prompt` 首句被多值选项吞掉（Claude Code `--allowedTools`）：首句前加 `--`；且带首句启动时，首句的输入事件出现前状态保持 `starting`，不再误报 `idle`。
3. Codex 正常退出要收尾好几秒（实测 7.6 s），原先 5 秒就发 SIGTERM：改为等 20 秒；实测 `stop` 以 `keys`、退出码 0 结束。
4. `--prompt` 首句的输入来源报成 `agent`：改为 `send`。
5. 鼠标悬停移动（1003 上报，Claude Code 全屏渲染时开）被算作人工操作，导致 send 一直 `human_active`：只移动不按键不再算。
6. skill 描述：写明用户要的是可接入的独立会话，不是自己作答、也不是内置子代理。

**观察（不是 corral 的问题）**
- 登录 shell 的 `CLAUDE_CODE_NO_FLICKER` 现在会传给 agent，Claude Code 以全屏渲染运行（M2 的预期效果）。
- skill 示例 `-- claude` 不带模型，内层 agent 用用户默认模型（本机是 Opus）。
- haiku 偶尔把极短的「只回复 A」当成提示词注入拒绝。
