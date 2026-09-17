# corral 使用指南（给人看）

这份是日常用的说明：怎么开 agent、怎么从不同终端接进去、怎么让几个 agent 配合。命令的精确字段和退出码以 [CONTRACT.md](CONTRACT.md) 为准；给 agent 看的那份是 `corral guide`；设计理由在 [DESIGN.md](DESIGN.md)。

## 1. 先记住的模型

- **一个 agent 一个栏位**，用名字操作，如 `demo/alice`。名字用 `/` 分段，按项目加前缀。
- **所有 agent 都用 corral 开**，包括你日常聊天的主对话。不支持一部分在 corral 里、一部分直接敲 `claude` 开的混合用法。
- **窗口只是观看者**。agent 由栏位托着，在哪个终端里看、看不看、关掉窗口，都不影响它跑。
- **状态来自 agent 自己的钩子**，不读屏。所以 corral 知道它是 idle、working 还是 blocked，但不知道屏幕上现在画的是输入框还是菜单。
- **实例编号**：同名 agent 退出后再开，名字一样、实例编号不同。给它送话前对一下编号，别把话塞给一个毫不知情的新对话。
- corral 只提供机制。「开谁、送什么、空闲后做什么」由上层决定：日常是对话里的 agent 自己判断，固定流程则是建在 corral 之上的 harness。

## 2. 安装与检查

```sh
ln -s <仓库>/bin/corral ~/.local/bin/corral   # 软链即可，仓库更新自动生效
corral --version                               # {"ok": true, "version": "…", "contract": "1"}
corral install-skills                          # 把 skill 装进 Claude Code 和 Codex，会先列路径再问 y/N
```

装好 skill 后，一个用 corral 开的 agent 听到「开一个 Codex 看一下」「交给另一个 agent」这类说法，会按 skill 的指引用 `corral start` 开一个新 agent、送话、等回复。触发靠 skill 描述里的说法，不是硬规则。agent 发现自己不是用 corral 开的，会拒绝委派并提醒你先用 corral 启动它。委派方如果是 Codex，本身要 `--yolo` 启动，沙箱里用不了 corral。skill 是全局的，对这台机器上所有 agent 生效；只想给某个项目用，改用 `corral install-skills --project <目录>`。SKILL.md 改了才需要重装。

所有命令输出一行 JSON，看 `ok` 和退出码。缩进看的话接 `| python3 -m json.tool`。

## 3. 单个 agent 的一生

### 开

**要让 corral 管的 agent，必须用 `corral start` 开。** 直接在终端里敲 `claude` 或 `codex --yolo` 开的 agent，corral 完全看不见：没有栏位托着它，钩子也没注入，`ls`、`send`、`attach` 都找不到它。包括你日常聊天的主对话在内，所有 agent 都这样开，见第 4 节。

```sh
corral start demo/alice --cwd ~/proj -- claude
corral start demo/alice --cwd ~/proj -- codex --yolo
corral start demo/ask --unique --cwd ~/proj --prompt "看一下这个想法：……" -- claude
```

- `start` 立即返回一行 JSON，agent 在后台的栏位里跑，此时你看不到它的界面。想像平时那样面对面用，就紧接着 `corral attach demo/alice`；或者预先在旁边挂一个 `corral attach --wait demo/alice`，start 一完成就自动接上。
- `--` 后面是 agent 的完整命令，模型等参数照常跟在后面，如 `-- claude --model haiku`。
- Codex 一律 `--yolo`：它默认开沙箱，沙箱里连不上 corral，也写不了仓库外的文件。
- `--prompt` 是第一句话，由 agent 自己提交。**新开的 agent 送第一句只能走这里**，Codex 在第一次提交前没有任何事件，`send` 会被拒。
- `--unique` 把名字当前缀，自动补后缀（`demo/ask-3`），以输出里的 `name` 为准。要同时开好几个就用它。
- `--env KEY=VALUE` 给 agent 补环境变量。agent 的环境取自你的登录 shell，不是当前终端的。
- 同名已在跑：退出码 5。启动后卡在信任框等对话框：状态一直是 `starting`，接进去处理。

### 看、插话

```sh
corral attach demo/alice          # 接进去，按 Ctrl-] 断开，agent 继续跑
corral attach --wait demo/alice   # 名字还不存在就等着，一出现自动接上；agent 退出后回到等待
```

- 第一个接入的窗口能打字，之后的只读；终端尺寸跟着能打字的那个走。
- 在任意终端软件、任意标签里都能接，接完退出，换个终端再接，agent 不受影响。
- 「让它自动出现在旁边」：先开个分屏挂 `attach --wait`，再 start。分屏由你的终端软件做，corral 不管布局。
- 人在接入窗口里操作过之后的 30 秒内，别的 agent 的 `send` 会避让（退出码 8）。只看不动不影响。

### 断开和退出不是一回事

Ctrl-] 是按住 Control 再按右方括号键 `]`。它由 `corral attach` 自己截下，不会送进 agent。

| 你做的 | 谁收到 | 结果 |
|---|---|---|
| 按 Ctrl-] | `corral attach` | 只断开这个窗口。agent 继续跑，名字还在，随时再 attach |
| 直接关掉窗口，或退出整个终端软件 | `corral attach` | 等于断开，agent 继续跑 |
| 在里面输入 `/exit`，或连按两次 Ctrl-C | agent 本身 | agent 真的退出，这段对话结束，栏位跟着关，名字从 `ls` 里消失 |
| `corral stop <名字>` | corral | 同样是结束 agent，但由 corral 用这种 agent 自己的方式退出，等它退干净才返回，并报告退出码 |

- 暂时离开：按 Ctrl-] 或直接关窗口。结束它：`corral stop`。
- `/exit` 也能结束，只是没有 `stop` 那样的确认。接进去后习惯性输入 `/exit` 是最容易误关 agent 的操作。
- `attach --wait` 里按 Ctrl-] 会连等待一起退出；agent 自己退出时，它回到等待，下次同名 start 再自动接上。
- 某个终端软件把 Ctrl-] 占用了，就直接关窗口，效果一样。

### 查

```sh
corral status demo/alice   # state、last_tool、turn_started、last_input_source、attached、idle_for…
corral ls                  # 所有活着的 agent，顺手清掉已死的残留
corral where demo/alice    # cwd、agent_pid
corral read demo/alice     # 最近的原始输出，只作排查
```

状态值：`starting` 启动中或卡在对话框 / `idle` 这一轮结束 / `working` / `blocked` 弹了权限框或提问框 / `exiting` / `unknown` 不认识的 agent。

`idle` 只表示这一轮结束，agent 可能自己再开一轮（后台命令结束后注入通知就是这样）。看 `last_input_source` 是 `send`、`human` 还是 `agent`。

### 一眼看所有 agent：看板

```sh
<仓库>/tools/board                 # 每 3 秒刷新，Ctrl-C 退出
<仓库>/tools/board --prefix demo/  # 只看某个前缀
<仓库>/tools/board --once          # 只打印一次
```

- 每个 agent 一行：名字、种类、实例编号、状态、正在做（在干活或卡住时显示最近的工具和这一轮跑了多久）、多久没输出、接入窗口数、最近一次输入的来源、终端标题。
- 只读：它只调用 `corral ls` 和 `corral status`，不操作任何 agent。适合在一个分屏里一直开着。
- 它是配套工具，不是 `corral` 的子命令，不在 PATH 上。常用的话，在你自己的 shell 配置里给它起个别名。

### 说话

```sh
corral send demo/alice "读 TASKS.md，做任务 1，改完跑测试。"
corral keys demo/alice esc          # 打断
corral keys demo/alice down enter   # 回答对话框；不看状态，后果自负
```

- `send` 只在 idle 时送，以 agent 输入事件里的文字和送出的一致为送达确认。多行、代码块、特殊字符都逐字送达。
- 长内容（几十 KB）写进文件，`send` 只说一句「读某某文件」。
- 送成功但 `merged_with_draft: true`：输入框里原本有人留的草稿，你的话接在后面一起提交了。agent 收到了，**不要重送**。
- `keys` 之后 agent 要过一会儿才反应，紧接着 `send` 可能退 7，先 `status` 再送。

### 等、取回复

```sh
corral wait demo/alice --timeout 90            # 等这一轮结束，result: idle / blocked
corral wait demo/alice --timeout 90 --quiet 120 # working 时连续 120 秒没动静也返回，result: stopped-quiet
corral reply demo/alice                        # 上一轮最后一条回复的原文
```

- 超时退出码 4，再等一次就行。`wait` 默认 600 秒；在 agent 的 shell 工具里调用时用短超时循环。
- `blocked`：接进去处理权限框或提问框，处理完再 wait。
- `stopped-quiet`：多半是人在窗口里按 Esc 打断了 Claude Code，它打断时没有事件，只能靠静默超时判断。Codex 有专门的打断事件，会直接变 idle。

### 停

```sh
corral stop demo/alice   # 等 agent 真正退出才返回；输出 exit_code 和 stopped_by
```

Claude Code 约 1 秒。Codex 用它自己的方式（连按两次 Ctrl-C）退出，收尾时间随会话内容增长，跑过几轮后要近 30 秒，corral 最多等 60 秒再升级到信号。`stopped_by` 是 `keys` 说明正常退出；是 `SIGTERM` 说明没等到。

agent 不会自己退出，用完记得 stop；`corral ls` 看还开着哪些。主对话替你开的 agent 默认不关，你说关才关，见第 4 节。

## 4. 多 agent：在对话里开另一个 agent

corral 的日常用法不是写脚本，而是**对你手头的 agent 说一句话，它自己去开另一个 agent 干活**。前提是 skill 已装（第 2 节）。

### 怎么说

对正在和你聊的 Claude Code 或 Codex 说：

- 「开一个 Codex 看一下 `src/parse.py` 的错误处理，把它的意见告诉我。」
- 「把这个方案交给另一个 Claude Code 核对一下，有分歧的地方列出来。」
- 「再问它一下第二点。」（追问同一个 agent）
- 「同时开三个，各审一个模块。」
- 「让 demo/ask-3 接着把第二点展开写。」（让开着的 agent 继续做）
- 「把 demo/ask-3 关掉。」（默认不关，要你说）

### 它会做什么

1. `corral start` 开一个新 agent，名字自己起（如 `demo/ask-3`），第一句用 `--prompt` 带过去。
2. `wait` 等对方答完，`reply` 取回复原文，整理后告诉你。
3. **默认不关**。把结果告诉你时，会说明对方还开着、叫什么名字。你想让它接着做就直接说，不用了说「关掉」。

### 主对话也用 corral 开

你日常聊天、让它去开别的 agent 的那个主对话，同样用 corral 开：

```sh
corral start demo/main --cwd ~/proj -- claude
corral attach demo/main
```

- 离开按 Ctrl-]，要结束时 `corral stop demo/main`。别在里面输入 `/exit`，见第 3 节「断开和退出不是一回事」。
- 好处：关窗口不丢、换终端接着聊，而且它有名字，别的 agent 做完能提醒它（下一小节）。
- 直接敲 `claude` 开的主对话，skill 会让它拒绝委派，提醒你先用 corral 启动。

它开出来的 agent 是独立的会话，有自己的上下文，看不到你和它的对话。所以要交代清楚的内容，让它在提示里写全，或者先写进文件再让对方「读某某文件」。长材料一律走文件。

### 交出去不等：做完提醒

默认情况下，主对话把活交出去后会在前台等对方答完，这期间没法和它聊别的。短问题这样就够了。要跑很久的活，这样说：

- 「交给 Codex 跑全量测试，做完告诉我，我们先聊别的。」

它会做三件事：

1. `corral start` 开对方，交代的话末尾带上「命令都在前台跑完，全部做完后回复最后一行写 DONE」。
2. 运行 `corral send <自己的名字> "<提醒的话>" --after <对方名字> --timeout 3600`。这条命令立即返回，主对话结束这一轮，你接着和它聊。
3. 对方这一轮结束时，那句提醒像你打字一样送进主对话。主对话去看对方的回复：有 DONE 就把结果告诉你，对方仍然开着；对方其实还在干，就再挂一次；对方停了但没 DONE，就让你去 attach 看。

要知道的几点：

- **提醒不是立刻到**。你正在和主对话聊、它还在干活，或者你 30 秒内在它的窗口里打过字，提醒会等一等再送，不会打断你。
- **可能被叫早**。corral 只知道对方「这一轮结束」，不知道「事情做完」。对方停下来问问题、把命令放到后台先结束这一轮，都会触发提醒，所以要靠 DONE 判断。叫早的代价只是主对话多醒一次。
- **别在主对话的输入框里留没提交的草稿**。提醒送进来时会和草稿拼在一起提交。
- **没被提醒时自己去查**。提醒送没送成功没有记录，`corral status <对方名字>` 一看就知道。
- 对方卡在对话框、退出、或者等满 `--timeout`，也都会提醒。

### 让它接着做

对方做完后默认还开着，上下文都在。要它继续：

- **让主对话去说**：「让 demo/essay-1 接着做：……做完提醒我。」主对话用 `send` 把新任务交过去，再挂 `send --after` 等它做完。
- **你自己接进去说**：`corral attach demo/essay-1`，像平时一样打字，用完 Ctrl-] 断开。主对话不知道你让它做了什么，要结果时让主对话去 `reply`。你在对方窗口里打字的 30 秒内，主对话往对方送话会被挡回。
- **已经被关掉了**：Claude Code 退出时会打印 `claude --resume <会话编号>`。不要直接在终端里 resume，而是用 corral 接着开：`corral start demo/essay-1 --cwd <原来的目录> -- claude --model sonnet --resume <会话编号>`。这个用法还没实测过。

### 你怎么看、怎么插手

- `corral ls` 列出它开了哪些；agent 也会告诉你名字。
- 想盯着看：`corral attach <名字>`。接进去可以直接和对方说话；接完 Ctrl-] 断开，不影响它们继续。
- 想让它开出来的 agent 自动出现在旁边：让它用固定名字，比如说「用名字 demo/helper 开」，你事先在旁边挂 `corral attach --wait demo/helper`。`attach --wait` 只认完整名字，agent 默认用 `--unique` 起名会补上后缀，挂不上。
- 对方弹了权限框或提问框：agent 会告诉你「blocked，请接入处理」，不会替对方点。你 attach 进去点完，它接着等。
- 对方卡在启动对话框里：状态一直是 `starting`，同样接入处理。

### 两条边界

- agent 只碰它自己开的，不会给 `corral ls` 里别人开的送话，也不会 stop 它们。
- 一个 agent 只走一条通道：用 corral 开的，就只通过 corral 跟它说话，不再用别的渠道（会话之间的消息、子 agent 工具）碰它。

### 需要固定流程时

评审循环、请求和交付的交接、自动叫醒之类的固定流程，属于建在 corral 之上的 harness，不属于 corral，也不在这份文档里展开。corral 只提供命令，流程由 harness 决定。

## 5. 几个日常场景

corral 本身没有场景，场景来自你手上的活。下面这些不需要任何流程，说一句话就行。

**拿意见**

- **第二意见**。和你写代码的 agent 卡在一个设计取舍上，说「开一个 Codex 看看这个方案」。另一家模型的意见一两分钟回来，不用自己开新窗口复述上下文。
- **提交前的独立评审**。写完一段，说「开一个 Claude Code 审一下这个 diff，只报有证据的问题」。审的那个是干净会话，没被前面的对话带偏。
- **两家同问一题**。「分别开一个 Claude Code 和一个 Codex，把这个问题问一遍，把分歧列出来」。分歧点往往就是你该自己想的地方。
- **写完让另一个复述**。「开一个 agent 读这个 PR，用三句话说它做了什么」。复述得不对，说明代码或说明写得不清楚。

**分担活**

- **并行探路**。三个方向拿不准，说「同时开三个，各试一个，回来比较」。你只看结论。
- **交给便宜模型跑腿**。「开一个 haiku 把这批文件的注释翻一遍」，主对话的模型不用花在这上面。
- **给主对话瘦身**。主对话已经很长、上下文快满了，把独立的一块活外包出去：「开一个新的做这个子任务，做完把结果文件路径告诉我」。主对话保持干净。
- **测试和构建放到旁边跑**。「开一个在 worktree 里跑全量测试，跑完告诉我结果，我们先聊别的」。交出去不等，做完提醒，主对话不被阻塞。
- **在 worktree 里修 bug**。「开一个 agent 在 worktree 里复现并修这个问题」，主线工作区不受影响，修好再合。

**跨仓库**

- **问另一个仓库**。`--cwd` 指向另一个项目开 agent，「这个库的接口怎么用」，它带着那个仓库的上下文回答，比把文件粘过来准。
- **一个 agent 盯着另一个**。「等 demo/build 做完提醒你，再把它的回复整理给我」。主对话用 `send --after` 挂上提醒，你们接着聊，做完再处理。

**看和接**

- **长任务放着跑，换个终端接着看**。在一个终端里开的 agent，换台显示器、换个终端软件 `corral attach` 进去接着看。窗口关了它也不丢。
- **常驻的专职 agent**。开一个固定名字的「文档助手」或「测试跑手」一直挂着，谁需要就让它问一句。它有自己积累的上下文，不用每次重讲。
- **旁边一直开着观察窗**。约定一个固定名字，比如让 agent 开临时 agent 时都用 `demo/helper`，旁边挂 `corral attach --wait demo/helper`。每次开出来都自动出现在那里，你随时能看它在干什么，也能直接插话。同一时间只能有一个同名 agent，要并行开好几个时就换回 `--unique`，自己 attach。

先用前两条，用一段时间自然会冒出自己的用法。

## 6. 规矩

- **所有 agent 都用 corral 开**，主对话也不例外。
- **一个 agent 只走一条通道**。用 corral 开的 agent，送话、等待、停止都只用 corral。它仍然是普通会话，别的渠道（会话之间的消息、子 agent 工具）也能碰到它，但 corral 看不见那些输入，混用后状态和输入来源都会失真。
- **只碰自己开的**。`corral ls` 里别人开的不要送话，更不要 stop。
- **不读状态目录**（`~/.corral`）里的任何文件，只看命令输出。内部格式随时会改。
- **不替 agent 回答对话框**，`blocked` 交给人接入处理。
- `read` 是累积输出不是当前画面，判断屏幕只看尾部，而且只作排查，不要靠它做决定。

退出码速查（agent 报出退出码时对照）：

| 码 | 含义 | 怎么办 |
|---|---|---|
| 2 | 不在了 | 附 `exited` 里有退出信息；需要就重新 start |
| 3 | 送了没确认 | 不要重试回车，`status` 看状态，必要时接入去看 |
| 4 | 超时 | 再等一次 |
| 5 | 同名已在跑 | 换名字或 `--unique` |
| 6 | 在 Codex 沙箱里 | 用 `--yolo` 启动那个 Codex |
| 7 | 不是 idle | 先 wait |
| 8 | 人刚操作过 | 过 30 秒再试；确定要打断人才 `--force` |
| 9 | 版本不兼容 | 用对应版本的 corral stop 后重新 start |

## 7. 出岔子

- **一直 `starting`**：卡在信任框或别的启动对话框里，接进去处理；Codex 在第一句提交前也是 starting，所以第一句必须用 `--prompt`。
- **wait 返回 `blocked`**：权限框或提问框，接进去点；处理完状态会回到 `working`，再 wait。
- **`stopped-quiet`**：人打断了它。看窗口决定是重送还是作罢。
- **主对话一委派就变 blocked**：实测用 haiku 做主对话时，它运行 corral 命令会弹 Claude Code 的权限框，启动时加了允许 corral 命令的参数也一样；换成 sonnet 就没有。做主对话请用 sonnet 或更强的模型，Codex 用 `--yolo`。
- **交出去之后一直没被提醒**：`corral status <对方名字>` 看对方状态。对方还在干活，说明它没停；对方已经停了，多半是提醒送的时候主对话被重启过，直接让主对话去取回复。
- **send 退 3**：屏幕上可能是菜单或对话框把文字吞了。corral 不补发按键，因为补发的回车可能点中菜单项。接进去看。
- **stop 出来 `stopped_by: SIGTERM`**：Codex 没在 60 秒内收尾完，会话里未落盘的东西可能丢了。记下来，这是要放宽阈值的信号。
- **退 9**：新旧版本的 corral 混着用了。用启动它的那个版本 stop，再用新版 start。
- **整个终端软件退了**：agent 照常在跑，`corral ls` 找回来再 attach。

## 8. 边界

- 状态目录默认 `~/.corral`，环境变量 `CORRAL_HOME` 可改；同一台机器、同一用户看到的是同一个目录。agent 环境里带着 `CORRAL_HOME`、`CORRAL_NAME`、`CORRAL_INSTANCE`，agent 里再调 corral 看到的也是同一份。
- 钩子只随 corral 启动的那个 agent 生效，不写任何全局配置。你自己的全局钩子和插件照常对它生效。
- 不做的：前端、多窗格布局、终端模拟、会话恢复、远程机器、Windows。
