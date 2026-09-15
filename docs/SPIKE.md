# corral 试验

**目的**：用最小的栏位和 `corral` 命令，拉起真实的 Claude Code 和 Codex，回答设计里只能实测的问题，决定 corral 做不做、怎么做。

**原则**
- 试验代码放在 `spike/`，允许粗糙，**不是正式版**。正式版另起，按试验结论重写。
- 只用临时目录和一次性的 agent。试验用的工作目录和状态目录放在短路径下（macOS 上 Unix socket 路径不能超过 104 字节）。
- 能自动化的检查写成探测脚本，留下可重复运行的证据；需要人眼判断的，列成清单交给人。
- 真实 agent 会消耗额度：送的话尽量短（比如「只回复 OK」），能用便宜的模型就用便宜的模型。
- 每条检查记下：结论（通过 / 不通过 / 有条件通过）、证据（命令输出、事件文件片段）、发现的新问题。
- **测「输入框被挡住」时，不要用会保存设置的菜单**（如 Claude Code 的 `/model`）：菜单里的回车会把选择写进用户全局设置。需要故意触发权限请求的检查，先问人。

## 开始前要决定

- **试验用的 agent 带不带用户的全局钩子。** 用户本机的全局钩子可能有副作用（比如把审批请求转发到手机、发桌面通知）。
  - 带上：第 7 条「钩子追加」测得真实，但会触发那些副作用。
  - 不带：Claude Code 可用 `--setting-sources` 排除用户设置；Codex 可换一个独立的 `CODEX_HOME`（登录信息要另外处理）。干净，但测不到共存。
  - 建议：大部分检查不带；第 7 条单独带上跑一次，事先告诉人会触发什么。
  - **已定（2026-09-15）**：用户先清掉了有副作用的全局钩子，试验 agent **直接用用户的真实配置**，全部检查都带全局钩子。Claude Code 不排除用户设置；Codex 用真实的配置目录，不另建 `CODEX_HOME`、不复制登录信息，一律 `--yolo` 启动。剩下的副作用只有桌面通知，用户接受。需要故意触发权限请求的检查，先问人。

## 检查清单

**第一组：corral 本身能不能成立（任何一条不过，corral 不做）**

1. **接入与交互**：`corral attach` 后画面能正常重绘；能正常打字、点对话框；退出接入后 agent 继续跑。（需要人看）
2. **送达**：多行文字能完整送进输入框并提交；`corral send` 以「收到输入」事件确认送达；送不到时能明确报错。
3. **状态**：`corral status` 能稳定报出 starting / working / idle / blocked；不修改任何全局配置文件。
4. **断开存活**：关掉接入窗口（杀掉 attach 进程）再接回来，agent 还活着、对话还在。
5. **无常驻中心**：除栏位外没有常驻进程；所有 `corral` 命令跑完即退；启动 `corral start` 的进程退出后栏位仍在。
6. **沙箱与环境**：
   - 在 `codex sandbox -P :workspace -C <目录> -- corral start …` 里运行会被拒绝，并给出清楚的说明；
   - 在不受沙箱限制的进程里运行，拉起的 agent 能写仓库外的目录、能跑测试；
   - 新 agent 的环境里没有调用者的 Codex 专属变量、调用者的 corral 名字、其他终端工具的会话变量。
7. **钩子追加**：corral 按启动参数给 Claude Code 加的钩子，和用户全局设置里已有的钩子同时生效，不把它们顶掉。

**第二组：接到调用方之后好不好用（不过的，记录现象和替代办法，由人判断）**

8. **信任对话框**：在从未信任过的新目录启动时，`corral status` 能在限定时间内报出 starting（启动未完成），而不是一直「未知」或误报 idle。
9. **Esc 打断**：agent 干活时送 Esc，两家的事件分别是什么、状态变成什么；「最后一次输出时间」能否可靠地让调用方判断出空闲。**找不到可靠解法视为不过**（依赖状态叫醒 agent 的调用方会失灵）。
10. **Codex 钩子**：不改用户全局配置的前提下，corral 给 Codex 配的钩子能否生效（试 `-c` 覆盖、`--dangerously-bypass-hook-trust`、独立 `CODEX_HOME` 等）。**找不到可靠解法视为不过。**
11. **`corral attach --wait`**：在预先开好的分屏里挂着，目标 agent 被拉起时自动接上、退出后回到等待、再次拉起又接上。顺带确认 Ghostty 能否用命令在现有窗口里开分屏。（手感需要人看）

**第三组：大量使用的场景（不过，corral 不做）**

12. **临时委派来回一趟**：由 Codex 开一个 Claude Code、送话、`corral wait` 等完、`corral reply` 拿回干净回复；反过来由 Claude Code 委派给 Codex 也走一遍。回复里的多段文字、代码块都要完整。

## 判定

- 第一组、第三组任何一条不过：corral 不做，记下卡在哪。
- 第 9、10 条找不到可靠解法：corral 不做。
- 第 8、11 条不过：记下代价和替代办法，由人判断是否可以接受。
- 十二条都有结论，才开始写正式版。

## 结果

| # | 检查 | 结论 | 证据 / 备注 |
|---|---|---|---|
| 1 | 接入与交互 | 通过 | Codex 0.154（2026-09-15 人工检查）：第一次接入时 Ctrl-] 失灵（kitty 键盘协议，见问题 D），人关掉那个 Ghostty 分屏脱身，Codex 不受影响（又验证了一次第 4 条）；修好后重新接入：画面正常，接入窗口里打的首句「只回复 OK」触发 SessionStart + UserPromptSubmit、回复正常，改尺寸正常，Ctrl-] 能退出，退出后 shell 打字、粘贴正常。人的结论：「都正常」。 2026-09-15 人在 Ghostty 里 `corral attach demo/cc`（Claude Code 2.1.272，haiku，干净环境下没有用户 shell 里的渲染开关）：画面完整正常；在接入窗口里打字提交，钩子记到输入事件、回复正常，中文输入法可用；拖窗口、开关分屏时界面跟着重排；Ctrl-] 退出后 shell 打字、粘贴都正常。人的结论：「都正常」 |
| 2 | 送达 | 有条件通过（条件见问题 A、E） | Codex 0.154（gpt-5.6-luna，low，`--yolo`）：单行、多行 + 代码块完整送达，确认延迟 0.41 s；按问题 A 改过的 `send` 在 working 时以退出码 3 拒绝（`not idle: working`）。Claude Code 2.1.272（haiku）：单行、多行 + 代码块都完整送达，钩子里的 `prompt` 和送的文字逐字一致；确认延迟 0.4 s。多行用终端粘贴模式包起来，粘贴后隔 0.3 s 再送回车。输入框被菜单挡住时 `send` 以退出码 3 报「没收到输入事件」，但补发回车引出了问题 A |
| 3 | 状态 | 有条件通过（条件见问题 E、F、H；Codex 的 blocked 没测） | Claude Code 权限框（人在接入窗口里，经同意触发）：`send`「用 Bash 执行 echo ok > /tmp/crw/probe-cc.txt」→ PreToolUse(Bash) → 0.1 s 后 PermissionRequest(Bash)，状态 blocked；6 s 后再来一条 Notification（`permission_prompt`）。人故意停了 36 s 不点，期间一直 blocked、没有输出（`idle_for` 最大 35.7 s），没有误判。人点允许 → PostToolUse → working → Stop → idle，文件写成功。拒绝的情况没测。另外空闲约 60 s 后会来一条 Notification（`idle_prompt`），不影响状态。Claude Code 提问框（已知难点 3）：`send`「用 AskUserQuestion 工具问我红色还是蓝色」→ PreToolUse(AskUserQuestion) 后同一时刻来一条 PermissionRequest(AskUserQuestion)，状态 blocked（人停了约 5 s，期间没有误判）；人选完 → PostToolUse → Stop → idle，回复「红色」。所以第 9 条「working 且久无输出就当空闲」的判断不会碰上这两种弹框，它们都先变成 blocked。Codex 的权限框、提问框没测（试验 Codex 一律 `--yolo`，不弹审批）。Codex：Stop 里有 `last_assistant_message`；干活时调用工具有 PreToolUse / PostToolUse。**Codex 内部的记忆整理子 agent 继承同一套钩子**，事件混进同一个文件，一度把已经空闲的主会话报成 working（问题 F）；试验版改成只认主会话的 session_id 后恢复正确。Codex 的终端标题是「转圈字符 + 目录名」，空闲时只剩目录名。Claude Code：启动约 0.5 s 收到会话开始 → idle；送话后 working；回合结束 → idle。回合结束钩子直接带 `last_assistant_message`，`corral reply` 不用读对话记录。blocked 还没测（要故意触发权限请求，先问人）。终端标题能拿到（`✳ Claude Code`、干活时 `◐ …`、之后变成对话主题） |
| 4 | 断开存活 | 通过 | 真实 agent（`spike/t_attach_agent.py`，Claude Code 和 Codex 各跑一遍）：模拟窗口 40x120 接入，收到 5–7 KB 重绘；在接入窗口里打字提交「只回复 PING」，钩子照常记到输入事件（Codex 这是第一句）；SIGHUP + SIGKILL 杀掉接入进程后 agent 仍在、`attached` 归零；换成 30x90 再接入，重绘内容里有 PING；Ctrl-] 退出接入，退出码 0；之后 `send`「我上一句让你回复哪个词」，两家都回答 PING，对话还在。画面是否正确要人看（第 1 条）。假 agent：`spike/t_attach.py`，目标是 `sh`：在伪终端里跑 attach，设变量后 SIGHUP + SIGKILL 杀掉 attach，再接回来变量还在；接入尺寸 30x100、50x160 和接入中改成 45x150 都正确传到 agent；第二个接入者只读，输入被丢弃；Ctrl-] 退出接入，agent 继续跑。真实 agent 待测 |
| 5 | 无常驻中心 | 通过 | `corral start` 两次 fork + 新会话，栏位 ppid=1、自成会话；`start` 命令本身立即退出。所有命令跑完即退，`pgrep -f corral.py` 只剩栏位。同名二次启动退出码 5；agent 自己 `exit 7` → 栏位记下退出码 7 后退出；`stop` → 发 SIGHUP，agent 退出；手动 `kill -9` 栏位后 `ls` 清掉残留 |
| 6 | 沙箱与环境 | 有条件通过 | `codex sandbox -P :workspace -- corral start …` → 退出码 6，给出说明。故意去掉 `CODEX_SANDBOX` 硬起：栏位绑定 socket 时 `PermissionError`，起不来。新 agent 环境（本会话本身跑在另一个终端工具里，带着它的会话变量和 Claude Code 的会话变量）：只剩白名单变量 + `CORRAL_*`，没有 `HERDR_*`、`CLAUDE_CODE_*`、`CODEX_*`。问题见 B、C。Codex（`--yolo`）拉起后能写工作目录外的 `/tmp/crw/probe-cx.txt`，能跑 `python3`；它跑的命令里看不到调用方的会话变量（`CODEX_*` 几个是 Codex 自己给命令加的；`CLAUDE_CODE_NO_FLICKER` 来自 Codex 用登录 shell 重读 `.zshrc`，说明问题 B 只影响 agent 进程本身）。Claude Code（haiku，手动审批模式）：经人同意触发权限框，人点允许后写成了工作目录外的 `/tmp/crw/probe-cc.txt` |
| 7 | 钩子追加 | 通过（Codex 用项目层钩子代替用户层验证） | Claude Code：用户全局设置里的一个桌面通知钩子（挂在输入、回合结束上）在它自己的数据库里记下了试验会话的每一句输入和结束时间（包括 agent 自己注入的后台任务通知），同时 corral 用 `--settings` 加的钩子也全部记到，两边同时生效。Codex：用户层只有两条不在对应终端工具里就什么都不做的钩子，从外面观察不到，改在已信任的试验目录里放项目层钩子：`.codex/hooks.json`（输入、回合结束）+ `.codex/config.toml` 的 `[[hooks.Stop]]`，再用 corral 的 `-c hooks.Stop=…` 启动。三路都触发，`-c` 没有顶掉配置文件里同名的 `hooks.Stop`。Codex 把 `-c` 当作单独的配置层（界面提示里叫 `<session-flags>`），并提示「Interrupt 钩子超时被截到 3 s」。探针文件用完已删 |
| 8 | 信任对话框 | 通过（Codex 有问题 E 的歧义） | Claude Code：非 git 的 `/tmp/crc` 不弹框；新建的 git 仓库 `/tmp/crg` 弹「Quick safety check」（默认选中「No, exit」），弹框期间没有钩子事件、终端标题为空，`status` 5 s 内一直报 starting；没点就 `stop`，没有留下信任记录。调用方用「starting 超过 N 秒」判断启动未完成即可。Codex：Codex 在 `/private/tmp/crx` 弹信任框（全局虽然信任了 `/`，对子目录不生效）；弹框期间没有任何钩子事件，`status` 一直报 starting，没有误报 idle。`-c 'projects."…".trust_level="trusted"'` 覆盖**无效**，照样弹。经人同意在框里选 Yes，`~/.codex/config.toml` 只多了这个目录的两行信任记录（试验结束由人删）。Claude Code 在 `/tmp/crc` 没弹信任框，见问题 D |
| 9 | Esc 打断 | 有条件通过（Claude Code 靠「最后一次输出时间」） | Codex：干活时（正在跑 `sleep 30`）送 Esc，0.1 s 内收到专门的 `Interrupt` 事件，状态回到 idle；被打断的 `sleep` 留在 Codex 的后台终端里继续跑。Claude Code：Esc 后**没有任何钩子事件**，状态卡在 working。能用的信号：①最后一次输出时间：干活时每 0.2 s 采样，生成文字 34 s 里最长停顿 0.8 s，前台跑 20 s 工具时最长 0.3 s（转圈动画一直刷新）；打断后输出停住，只在约 4 s 时刷新过一次（状态栏），之后 28 s 以上没有输出。所以「working 且超过约 5 s 没输出」可以判为已停。②终端标题的首字符立刻从转圈字符变成 `✳`，但这是 agent 界面的含义，不是终端通用信息。③对话记录：出字之后打断会写一条 `[Request interrupted by user]`，出字之前打断什么都不写，不能单用。**待定**：弹出权限框、提问框时同样没有输出，要靠 PermissionRequest 等事件先把状态变成 blocked，这一点和第 3 条的 blocked 一起测 |
| 10 | Codex 钩子 | 通过（要带跳过信任的参数） | 不改任何全局配置文件（每次跑前后比对哈希）：①只用 `-c 'hooks.<事件>=[{hooks=[{type="command",command="…"}]}]'`：钩子被识别，但启动时弹「Hooks need review：7 个钩子是新的」，选「全部信任」会把哈希写进 `config.toml`，所以没选；②`-c` + `--dangerously-bypass-hook-trust`：不弹框，钩子直接生效，事件齐全（SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop、Interrupt）。和用户全局钩子是否共存放到第 7 条测。代价是那个参数名字就叫「危险」，要写进契约文档说明原因。**注意 Codex 的 SessionStart 要到第一次提交输入才触发**（问题 E） |
| 11 | attach --wait | 通过 | 2026-09-15 人工检查：用 Ghostty AppleScript `split (focused terminal of selected tab of front window) direction right with configuration {command:"python3 …/corral.py attach --wait demo/w"}` 在人当前窗口右侧开分屏，命令返回新终端编号，没有弹系统授权框。后台两轮 `start demo/w`（Claude Code）→ 4 s 后 `attached=1` → `stop` → 回到等待。人的结论：「都正常，两轮都自动接上又回到等待了」。此前的机制自动测： 假 agent 自动测：在伪终端里先挂 `attach --wait demo/w`，显示「等待 demo/w 出现…」；`start` 后自动接上，打字能到 agent（回显带本实例编号）；`stop` 后回到等待，attach 进程不退出；第二次 `start` 又自动接上。两轮都对。Ghostty 1.3.1 自带 AppleScript 字典，有 `split`（上下左右）和 `new surface configuration`（可指定命令），看起来能用命令在现有窗口里开分屏并直接跑 `corral attach --wait`，还没实际开（会在人的窗口里弹分屏，要人在场） |
| 12 | 临时委派来回 | 通过 | 两个方向都由真实 agent 自己在 shell 里跑 `start --unique` → `wait` → `send` → `wait` → `reply` → `stop`，问题是「两段话解释 Unix socket + 3 行 python 代码块」。①Codex → Claude Code：内层名字 `demo/ask-1`；`corral reply` 的 text 和 Claude 钩子里的 `last_assistant_message` 逐字相等（5 处空行、代码块完整），外层 Codex 最后原样转述，也逐字相等。外层 Codex 的第一句作为启动参数传入，自动提交，钩子事件正常（问题 E 做法 2 可行）。②Claude Code → Codex：外层 Claude 用 `--allowedTools "Bash(python3 <corral.py>:*)"` 放行 corral 命令，没弹权限框；内层 Codex 首句作为启动参数传入；外层最终回答和 Codex 钩子里的原文逐字相等，代码块完整；外层执行了 `stop`，Codex 5 s 后才退出（问题 G）。两次都没有改动全局配置文件（哈希比对） |

## 试验中发现的设计问题（待和人确认后改 DESIGN.md）

**A. `send` 的「没收到就补发」会误触界面里的选择**（2026-09-15，第 2 条）
- 经过：给 Claude Code 打开 `/model` 菜单挡住输入框，再 `corral send`。文字被菜单吞掉，没有收到输入事件；按设计补发的回车落在菜单上，等于「设为默认」，把用户全局设置里的默认模型改成了 haiku。已由人恢复。
- 本质：栏位只写字节，不知道此刻屏幕上是输入框还是菜单或对话框。第一次的回车本身也可能误触，补发只是把风险翻倍。
- 建议：
  1. `send` 之前先查状态，只在 idle 时送；working / blocked / starting 直接以约定退出码拒绝，由调用方决定等还是用 `keys`。
  2. 去掉盲目补发回车。确认失败就报错，不再自动做任何按键。
  3. 残留风险：状态是 idle，但人在接入窗口里打开了菜单，钩子看不见。可选对策：有人以可打字身份接入时，`send` 默认拒绝（或要求显式参数）。
- 试验版先按 1、2 改；3 待定。

**B. 干净环境的白名单丢掉了用户自己的变量，同时 PATH 带进了调用方的临时目录**（第 6 条）
- 丢掉的：用户在 shell 配置文件里给 agent 设的变量（本机例子：`.zshrc` 里的 Claude Code 渲染开关）。用户平时启动的 agent 有，corral 拉起的没有，界面行为不一样。其他 agent 相关的配置变量、API 地址等也会一起丢。
- 带进来的：PATH 原样继承，里面有调用方所在 agent 会话临时加的目录（本机例子：调用方 Claude Code 给插件加的一串 bin 目录）。
- 可选做法（要人定）：
  1. 保留白名单，另加调用方可传的 `--env KEY=VAL` / `--pass KEY`：简单可预测，但用户要自己知道缺什么。
  2. 改黑名单：继承调用方环境，只剥掉已知的会话变量（`CODEX_*`、`CORRAL_*`、agent 自己的会话变量、其他终端工具的会话变量）：保留用户变量，但名单永远列不全，漏一个就串到别的会话。
  3. 用用户的登录 shell 重建环境（`$SHELL -l -i -c 'exec agent …'` 或先读一次登录 shell 的 `env`）：最接近「用户自己在新终端里敲命令」，PATH 也是干净的；代价是 shell 配置慢或会打印东西、交互式 shell 的副作用。
- 倾向 3（启动时从登录 shell 取一次环境，再剥会话变量）加 1 兜底，待试。

**C. 沙箱里连栏位失败报「不存在」，容易误导**（第 6 条）
- 沙箱里 `corral status` 连不上一个正在跑的栏位，报的是 not found。调用方会以为 agent 没了，可能去重新启动。
- 建议：所有命令（不只是 `start`）发现 `CODEX_SANDBOX` 就用同一个退出码拒绝。

**D. 其他观察**
- 在从未信任过的新目录（`/tmp` 下）启动 Claude Code，**没有弹信任对话框**，直接进入输入框；`~/.claude.json` 里该目录 `hasTrustDialogAccepted` 仍是 false。换成新建的 git 仓库目录就会弹（见第 8 条）。
- haiku 不支持 auto mode，Claude Code 自动落到手动审批模式。和 corral 无关，但用便宜模型试验时工具调用会弹审批。
- 接入时只转发字节，agent 之前打开的终端模式（粘贴模式、焦点上报、备用屏幕、kitty 键盘协议等）新窗口不知道。试验版的栏位顺带记下这些模式，接入时先重放一遍，退出接入时还原。设计里栏位只记「终端标题」，这里多记了一类终端层面的状态，要不要写进设计看第 1 条结果。
  - 人工检查时暴露的连带问题：Codex 打开了 kitty 键盘协议（`ESC[>7u`），栏位重放给 Ghostty 后，Ghostty 把 Ctrl-] 编成 `ESC[93;5u` 发过来，attach 只认单字节 `0x1d`，**退出接入键失灵**，按键被转给了 agent。Claude Code 不开这个协议，所以没遇到。试验版已改成两种编码都认。结论：只要重放键盘协议，退出接入键就必须按协议解析，设计里要写明。
- `corral read` 对 Claude Code 基本不可读（界面用光标移动代替空格），符合设计里「只作排查用」。

**E. Codex 的会话开始事件要到第一次提交输入才触发**（第 3、10 条）
- Codex 启动完、界面已经能输入，但在第一次提交之前没有任何钩子事件。按设计的状态规则会一直报 starting，和「卡在信任框 / 钩子审核框」分不出来。
- 连带：改过的 `send` 只在 idle 时送，于是**新开的 Codex 永远送不进第一句话**。试验里是用 `keys` 手打的。
- 另一次实测：启动 4 s 后就用 `keys` 送首句 + 回车，Codex 还在加载（界面在显示钩子相关提示），文字进了输入框，回车却没提交（界面提示「tab to queue message」）。可见「看起来能输入」和「回车会提交」不是同一时刻。
- 竞态：第一句提交时 SessionStart 和 UserPromptSubmit 相隔约 20 ms 先后到达，中间状态会短暂报 idle；实测有一次 `wait` 正好读到这个空档，接着 `reply` 拿到空。状态规则要处理「会话开始后紧跟输入」这种情况（例如 Codex 的会话开始不单独算 idle，或 idle 要求稳定一小段时间）。
- 可选做法（要人定）：
  1. 状态增加「ready-unknown」：没有会话开始事件，但终端标题已被 agent 设过、输出静止超过 N 秒，就报这个状态；`send` 允许在这个状态下送，**但不补任何按键**，靠输入事件确认。风险：如果屏幕上其实是信任框，回车会替人选「Yes」（会写配置），违反「不替 agent 回答对话框」。
  2. 让 `start` 在拉起 Codex 时直接带首句话（Codex 命令行支持把提示词作为参数）：第一句由 agent 自己提交，不经过伪终端。缺点：首句只能在启动时给。
  3. 接受这个限制：第一句话由调用方用 `keys` 自己负责，契约里写明。
- 倾向 2：临时委派的场景本来就是「开一个、马上送一句」。

**F. agent 内部的子会话会用同一套钩子往同一个事件文件里写**（第 3 条）
- Codex 的记忆整理子 agent：session_id 不同，`transcript_path` 为空，工作目录是 `~/.codex/memories`，模型也不同。它的 UserPromptSubmit / PreToolUse 会被当成主会话的事件。
- 试验版做法：主会话 = 最近一次「工作目录和栏位一致、并且带对话记录」的会话开始；状态、`send` 的送达确认、`reply` 都只认这个 session_id。改完后状态、回复都正确。
- 设计要补一句：状态只根据主会话的事件计算。Claude Code 的 `/clear` 会换 session_id 并重新触发会话开始，按这个规则会自然切过去，还没实测。

**G. `stop` 用 SIGHUP 让 Codex 退出不可靠**（第 5 条；Claude Code 收到 SIGHUP 0.8 s 退出，退出码 129，没问题）
- Codex 不管在信任框里还是空闲时都不理 SIGHUP，都是 5 秒后被 SIGTERM 杀掉（退出码 -15），`stop` 实际要等 5.2 s。它的记忆子 agent 当时正在改 `~/.codex/memories`，被一起杀掉。
- 同名的 `start` 紧接在 `stop` 后面会撞上「already running」：`stop` 是异步的。
- agent 后台终端里起的进程（例：`sleep 30`）在 agent 死后还留着。
- 建议：`stop` 先用 agent 自己的退出方式（Codex、Claude Code 各自的退出命令或按键），等到栏位退出再返回，超时才依次发信号；契约里写明 `stop` 会等到栏位退出。「agent 留下的后台进程要不要清」要人定（按进程组杀可能碰到 agent 故意留的东西）。

**H. agent 会自己开新一轮，「idle」不代表不会再动**（第 3、9 条）
- Claude Code 把命令放到后台跑，回合结束（Stop，回复是「正在等待它完成」）；后台命令结束时，它自己注入一条 `<task-notification>…` 当作输入，触发 UserPromptSubmit，又跑了一轮才最终回复。
- 影响：①调用方 `wait` 等到第一个 idle 就去 `reply`，拿到的是中间回复；②`send` 如果只看「送出之后有没有输入事件」，可能被这条自动输入冒充送达。
- 试验版已改：`send` 要求输入事件里的文字和送出的文字一致才算送达（两家钩子都带 `prompt`，多行也逐字一致）。
- 设计要补：契约里说明 idle 只是「这一轮结束」；`status` 可以带上「这一轮的输入是不是调用方送的」（比如最近一次输入事件的来源：`send` 送的 / 人在接入窗口里打的 / agent 自己注入的），由调用方判断要不要继续等。

## 判定（2026-09-15）

十二条都有结论：通过 7 条（1、4、5、7、10、11、12），有条件通过 5 条（2、3、6、8、9），没有不通过。第 9、10 条都找到了可靠解法。按判定规则，**corral 做**，按下面的设计选择改 DESIGN.md 后写正式版。

## 设计选择结论（2026-09-15 和人确认）

| 问题 | 结论 |
|---|---|
| A 送话误触界面 | `send` 只在 idle 时送；去掉补发回车，确认失败就报错；送达以「输入事件里的文字和送出的一致」为准。有人以可打字身份接入时：**最近 30 s 内接入窗口里有人按过键**，`send` 以约定退出码拒绝（调用方稍后重试），另有强制参数；只开着窗口看、没按键，照常送。`status` 带接入人数和最后一次人工按键时间 |
| B 环境变量 | 启动时从用户的登录 shell 取一次环境，再剥掉会话变量（`CODEX_*`、`CORRAL_*`、agent 自己的会话变量、其他终端工具的会话变量），重新设置 corral 的身份变量；另留 `--env KEY=VAL` 兜底 |
| C 沙箱里报「不存在」 | 所有命令发现在 Codex 沙箱里都以同一个退出码拒绝 |
| D 终端模式 | 栏位记下终端模式（DEC 私有模式、kitty 键盘协议），接入时重放、退出接入时还原；退出接入键按键盘协议解析 |
| E Codex 首句 | `corral start` 带首句参数，按 agent 种类作为启动参数交给 agent 自己提交；之后的话用 `send` |
| F 子会话事件 | 状态、送达确认、取回复只认主会话的 session_id |
| G stop | 先用 agent 自己的退出方式，超时再依次发信号；`stop` 等到栏位真正退出才返回；agent 留下的后台进程不清，契约里写明 |
| 第 9 条 Claude Code 打断无事件 | `corral wait` 加可选参数 `--quiet N`（N 由调用方给）：只在状态是 working 时，连续 N 秒没输出就返回；结果单独标明为 `stopped-quiet`，和收到回合结束区分开。blocked 由事件先判出，不受影响 |
| H agent 自己开新一轮 | 契约写明 idle 只表示这一轮结束；`status` 带最近一次输入的来源（`send` / 接入窗口里的人 / agent 自己） |
