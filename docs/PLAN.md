# corral 正式版实现计划

依据：[DESIGN.md](DESIGN.md)（设计）、[SPIKE.md](SPIKE.md)（试验结论）。2026-09-15 人批准。
里程碑做完一个勾一个；改计划先改这里。

## 约束

- 只用 Python 标准库；命令和栏位要求 Python ≥ 3.11；`hook.py` 要求 Python ≥ 3.9（由 `/usr/bin/python3` 运行）。
- macOS 和 Linux；不做 Windows。
- 不安装：只提供 `bin/corral` 和无依赖的 `pyproject.toml`，装不装由人决定。
- `spike/` 只作参考，不直接复用；移植试验里验证过的做法。
- 每个行为先写测试并确认失败，再实现（测试用标准库 `unittest`）。
- M0–M7 不调用真实 agent，不花额度；M8 用人的真实配置和便宜模型，需要触发权限框的步骤先问人。

## 目录结构

```
bin/corral                 启动脚本（#!/usr/bin/env python3，把 src 加进路径；版本不够就报错）
pyproject.toml
src/corral/
  cli.py        参数解析、JSON 输出、退出码
  paths.py      状态目录（默认 ~/.corral，CORRAL_HOME 可改）、名字校验、socket 路径长度、目录和文件权限
  sandbox.py    沙箱识别（所有命令共用）
  client.py     连接栏位、请求 / 应答、协议版本协商
  protocol.py   握手（一行 JSON）+ 接入后的分帧；协议版本号
  spawn.py      start：加锁、--unique、复制 hook.py、试跑 /usr/bin/python3、两次 fork、等栏位就绪
  env.py        登录 shell 取环境 + 兜底剥会话变量 + --env
  pen.py        栏位事件循环（只 import 标准库，启动时一次性加载完）
  termmodes.py  终端标题 / 终端模式的记录、重放、还原；退出键解析；人工按键和终端自动回应的区分
  attach.py     接入客户端、--wait
  events.py     增量读事件文件（cursor）、只认主会话、状态机、最近一次输入的来源
  hook.py       钩子脚本（Python 3.9 兼容，只用 json/os/sys/time）
  agents/       base.py、claude.py、codex.py：启动参数（钩子、首句）、正常退出方式、事件映射
docs/CONTRACT.md      对外契约 v1
docs/AGENT_USAGE.md   给 agent 看的使用说明（corral guide 打印）
tests/
  fake_agents/        假 claude（读 --settings 执行钩子）、假 codex（用 tomllib 解析 -c）
  fixtures/           上一个协议版本的栏位实现（兼容测试用）
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

## 里程碑

- [ ] **M0 骨架**：cli、paths、sandbox、退出码；CONTRACT.md 初稿。
  验证：单元测试——名字校验、socket 路径超长拒绝、各命令在沙箱里一律退出码 6、退出码常量和契约一致。
- [ ] **M1 栏位 + start / ls / where / read / status（终端层字段）**，被测对象是普通命令（`sh`）。
  验证：集成测试——同名加锁（5）、`--unique`、start 退出后栏位仍在且父进程是 1、agent 退出码记录、手动杀栏位后 ls 清残留；**权限**：状态目录各层 0700，lock / meta.json / sock / exit.json / pen.log 为 0600（hook.py、events、cursor 的权限在 M4 补测）；栏位协议版本出现在握手和 meta.json。
- [ ] **M2 环境**。
  验证：单元测试用假 `SHELL` 脚本——配置往外打印、配置慢触发超时退回白名单并带警告、会话变量被剥、`--env` 覆盖；集成测试检查 agent 实际拿到的环境。
- [ ] **M3 attach / attach --wait**、终端模式重放和还原、退出键、人工按键时间。
  验证：伪终端测试——尺寸传递和改尺寸、第二个接入者只读、Ctrl-] 的单字节和 CSI u 两种编码、焦点事件 / 光标位置报告不更新人工按键时间而普通按键和鼠标会、`--wait` 两轮自动接上又回到等待。
- [ ] **M4 钩子、适配器、事件、状态**。
  验证：hook.py 用 `/usr/bin/python3` 跑通、不往标准输出写、只用允许的模块（测试检查 import）；钩子命令不含仓库路径和 start 时的 Python 路径；hook.py / events / cursor 为 0600；假 agent 测试——主会话过滤（子会话噪声）、会话开始和输入乱序、agent 自己开新一轮、权限请求变 blocked、打断事件；**增量读**：大事件文件只读新增部分（在已读位置之前写入坏数据不影响结果）、半行不消费、实例变化和文件变短重算、两个 status 并发结果一致；**兼容**：事件格式上一个版本可读，不认识的版本退出码 9。
- [ ] **M5 send / keys / wait --quiet / reply / start --prompt**。
  验证：假 agent 测试——非 idle 拒绝（7）、30 秒内人工按键拒绝（8）和强制参数、终端自动回应不触发 8、按文字确认送达、确认失败退出码 3 且没有补发按键、输入来源三种、`stopped-quiet` 只在 working 时出现、`--prompt` 由假 agent 自己提交、wait 在「会话开始比输入早几十毫秒」时不提前返回。
- [ ] **M6 stop 与协议兼容**。
  验证：假 codex 不理 SIGHUP 时按顺序升级信号；stop 等到栏位退出才返回，之后立刻同名 start 成功；用 `tests/fixtures/` 里上一个协议版本的栏位跑 status / send / attach / stop；栏位报不认识的协议版本时退出码 9。
- [ ] **M7 文档**：CONTRACT.md 定稿（命令、JSON 字段、状态值、退出码、兼容规则、`--dangerously-bypass-hook-trust` 的原因）、AGENT_USAGE.md、`corral guide`。
  验证：检查脚本比对 CLI 实际的命令 / 退出码和契约文档一致；按 AGENT_USAGE.md 走一遍委派流程（假 agent）。
- [ ] **M8 真实 agent 冒烟**（人的真实配置、便宜模型；每次前后比对全局配置文件哈希；需要权限框的步骤先问人）。
  验证：SPIKE 第 2、3、4、9、12 条再跑一遍；确定 Codex 的正常退出方式；验证单行也走粘贴模式；验证钩子副本在删除 / 移动仓库副本后仍工作（用仓库的临时副本启动）。
