# corral

[English](README.md) | **简体中文**

给交互式 AI 编程 agent 用的极简托管基础设施，支持 Claude Code、Codex、pi 和 omp。

每个 agent 关在自己的一个小进程里，叫**栏位**。调用方按名字打开 agent、往里送话、查状态、等这一轮结束、取回复；人随时可以从任何终端接进去看、点对话框、插话。关掉窗口，agent 照样跑。

```sh
corral start demo/alice --cwd ~/proj -- claude    # 在后台开一个 agent
corral attach demo/alice                          # 接进去看；按 Ctrl-] 断开，agent 继续跑
corral send demo/alice "读 TASKS.md，做任务 1。"
corral wait demo/alice --timeout 600              # 等这一轮结束
corral reply demo/alice                           # 上一轮的回复，干净的文字
corral stop demo/alice
```

## 为什么用 corral

- **只提供机制，不带流程。** corral 负责打开、送话、查看、关掉 agent。开谁、说什么、agent 空闲以后做什么，由调用方决定：另一个 agent、一段脚本，或者你在上面搭的 harness。
- **一个 agent 一个栏位，没有中心服务。** 每个 agent 由自己的小进程托着，一个栏位出事只影响一个 agent。命令跑完就退出，状态存在文件里。
- **状态来自 agent 自己的钩子，不读屏。** corral 靠钩子事件知道 agent 是 `idle`、`working` 还是 `blocked`，agent 界面改版也不会把它弄坏。
- **保留 agent 自己的终端界面。** 不走无头模式，也不用编辑器协议代替它的界面，接进去看到的就是原样。
- **只用 Python 标准库。** 不装第三方包，也不依赖终端复用工具。唯一的例外：某家 agent 只接受自己语言写的钩子时（pi 和 omp 的钩子是 TypeScript 文件，由 agent 自带的运行时执行，不引入任何依赖）。
- **不改任何全局配置。** 钩子只注入 corral 启动的那个 agent，你自己的全局设置、钩子和插件照旧。
- **契约小，带版本号。** 每条命令输出一行 JSON，退出码有文档。调用方不许读 corral 的内部文件。

## 状态

- 已在日常使用。各家 agent 每个版本的真实冒烟结果记在 [docs/ROADMAP.md](docs/ROADMAP.md)（R6）。最近一次 Claude Code 冒烟发现的已知问题：用 `corral send` 送的较长的话，到了 agent 那里会被标成粘贴内容，模型偶尔不照做里面的指令；用 `--prompt` 送的第一句不受影响。
- 在 macOS 上开发和测试。Linux 没测过。不支持 Windows。
- 契约版本：`1`（`corral --version`）。

## 支持的 agent

| agent | `--` 后面写 | 说明 |
|---|---|---|
| Claude Code | `claude` | |
| Codex | `codex --yolo` | 一律 `--yolo`：Codex 默认开沙箱，沙箱里连不上 corral |
| pi | `pi` | 本身没有权限框；扩展弹出的对话框显示成 `blocked` |
| omp | `omp --approval-mode yolo` | 不要用家目录当 `--cwd` |

其他命令也能开、能接进去，只是没有钩子，状态报 `unknown`。

## 环境要求

- Python 3.11 或更新
- macOS
- 要用的 agent 命令行工具，已安装并登录

## 安装

```sh
git clone https://github.com/firegnu/corral.git
ln -s "$PWD/corral/bin/corral" ~/.local/bin/corral   # 软链接，仓库更新自动生效
corral --version                                     # {"ok": true, "version": "…", "contract": "1"}
```

可选：装上 **corral 技能**，让你的 agent 自己会用 corral（见[在对话里委派](#在对话里委派)）：

```sh
corral install-skills --dry-run   # 只列出会写哪些文件
corral install-skills             # 给 Claude Code 和 Codex 各写一个 SKILL.md，写之前问 y/N
```

只写 `~/.claude/skills/corral/SKILL.md` 和 `~/.agents/skills/corral/SKILL.md` 两个文件（pi 和 omp 也读后一个）。只想给某个项目用，加 `--project <目录>`；卸载加 `--remove`。

## 核心概念

- **一个 agent、一个栏位、一个名字。** 名字用 `/` 分段，比如 `demo/alice`，按项目加前缀分组。
- **所有 agent 都用 corral 开，包括你日常聊天的主对话。** 直接敲 `claude` 开的 agent，corral 完全看不见：没有栏位托着，也没有钩子。不支持两种混用。
- **窗口只是观看者。** 在哪个终端看、看不看、关不关窗口，都不影响 agent。
- **实例编号。** 同名 agent 退出后再开，会拿到新的实例编号。送话前核对一下，别把话交给一个什么都不知道的新对话。

整体结构：

```
                       钩子 ──► 事件文件 ──► corral status / wait / reply
                         │
   agent ◄──► 伪终端 ◄──► 栏位 ◄──► corral attach ◄──► 任意终端窗口
                         ▲
                         └──── corral send / keys / stop
```

## 命令

| 命令 | 作用 |
|---|---|
| `corral start <名字> --cwd <目录> [--prompt <文字>] [--unique] [--env K=V] -- <agent 命令>` | 在它自己的栏位里开一个 agent，立即返回。`--prompt` 是第一句话（给新开的 agent 送第一句只能走这里）。`--unique` 自动补一个不重复的后缀，如 `demo/ask-3`。 |
| `corral attach [--wait] <名字>` | 把当前终端接到 agent 上，Ctrl-] 断开。`--wait` 等这个名字出现再接，agent 重开后自动再接上。 |
| `corral send <名字> <文字> [--after <另一个>]` | 只在 agent 空闲时送，靠它的输入事件确认送达。`--after` 立即返回，等另一个 agent 这一轮结束再送进来。 |
| `corral keys <名字> <按键>...` | 送原始按键（`esc`、`enter`、`down`、`text:…`），不做任何检查。 |
| `corral status <名字>` | 状态、实例编号、最近的工具、这一轮开始的时间、最近一次输入的来源、接入窗口数。 |
| `corral wait <名字> [--timeout N] [--quiet N]` | 等这一轮结束，返回 `idle`、`blocked`、`stopped-quiet` 或 `unknown`。 |
| `corral reply <名字>` | 上一轮最后一条回复，干净的文字。 |
| `corral ls` | 列出所有活着的 agent，顺手清掉已死的残留。 |
| `corral where <名字>` | 工作目录和 agent 的进程号。 |
| `corral read <名字>` | 最近的原始输出，只用来排查。 |
| `corral stop <名字>` | 用这家 agent 自己的方式让它退出，等它退干净，报告退出码。 |
| `corral install-skills` | 给 Claude Code 和 Codex 装 corral 技能（先问）。 |
| `corral guide` | 打印给 agent 看的使用说明。 |

状态值：`starting`（启动中，或卡在「是否信任这个目录」这类启动对话框上）、`idle`（这一轮结束）、`working`、`blocked`（弹了权限框或提问框，等人处理）、`exiting`、`unknown`。

`idle` 只表示这一轮结束。agent 可能自己再开一轮，比如后台命令跑完的时候。看 `last_input_source` 可以知道最近一次输入来自 `send`、人（`human`），还是 agent 自己（`agent`）。

退出码：

| 码 | 标识 | 含义 |
|---|---|---|
| 0 | `ok` | 成功 |
| 1 | `error` | 用法错误或内部错误（细节在 JSON 的 `error` 字段） |
| 2 | `not_found` | 没有这个 agent，或者已经退出 |
| 3 | `not_delivered` | 送了但没确认送达。不要盲目重送，接进去看 |
| 4 | `timeout` | 超时，再等一次 |
| 5 | `exists` | 同名 agent 已在跑 |
| 6 | `sandbox` | 在 Codex 沙箱里调用 |
| 7 | `not_idle` | agent 不是空闲状态 |
| 8 | `human_active` | 最近 30 秒内有人在接入窗口里打过字 |
| 9 | `incompatible` | 栏位协议或事件格式的版本不兼容 |

## 断开不等于退出

| 你做的 | 结果 |
|---|---|
| 按 Ctrl-] | 只断开这个窗口，agent 继续跑 |
| 关掉窗口，或退出整个终端软件 | 等于断开 |
| 在 agent 里输入 `/exit`，或连按两次 Ctrl-C | agent 真的退出，这段对话结束 |
| `corral stop <名字>` | agent 干净地退出，corral 报告退出情况 |

接进去以后习惯性地输入 `/exit`，是最容易误关 agent 的操作。

## 在对话里委派

corral 的日常用法不是写脚本，而是对正在聊的 agent 说一句话，比如：

- 「开一个 Codex 看一下 `src/parse.py` 的错误处理，把它的意见告诉我。」
- 「分别问一个 Claude Code 和一个 Codex 这个问题，把分歧列出来。」
- 「把全量测试交给另一个 agent 跑，做完告诉我，我们先聊别的。」

装了 corral 技能以后，agent 会用 `corral start` 开一个新的，等它答完，取回复告诉你。长任务它不会干等：运行 `corral send <自己> "<提醒>" --after <对方>` 后就结束这一轮，对方这一轮结束时，提醒像你打字一样送进来。对方的回复最后一行写 `DONE`，这样能分清是被叫早了还是真做完了。

它开出来的 agent 默认不关，你说关才关，所以可以接着追问，也可以自己接进去和它说话。

## 看板

`tools/board` 在终端里显示所有 agent。把终端分成左右两格，各运行一次：

```sh
<仓库>/tools/board            # 左格：面板，状态表；按 r 显示选中 agent 的上一轮回复
<仓库>/tools/board --viewer   # 右格：显示器，面板选了谁就接入谁
```

- 按名字前缀分组。每行显示：状态、正在做什么（最近的工具和这一轮跑了多久）、多久没输出、接入窗口数、最近一次输入的来源、目录、标题。
- 行首标记：红色 `!` 卡住，黄色 `?` 疑似卡住，青色 `●` 做完了一轮你还没看，绿色 `▶` 正在右格显示。
- 左格窄时不藏信息，每个 agent 折成几行；agent 多了有滚动条，藏住的写在框边上。
- 按键：↑↓ 或 j / k 选，回车或鼠标点一下在右格显示，`r` 显示或隐藏上一轮回复，`s` 按状态排序，`x` 再按 `y` 关掉它，`q` 退出。
- 选项：`--prefix demo/`、`--bell`、`--once`。

它是配套工具，不是 `corral` 的子命令；常用的话在 shell 里起个别名。

## 编排技能：corral-dispatch（可选）

[`corral-dispatch-skill/`](corral-dispatch-skill/) 是给**主控** agent 用的技能：把开发工作拆成任务文件，每件活派给一个 Claude Code、Codex 或 pi，各在自己的分支和 git worktree 里做；主控审查结果，出错代价大的请另一家交叉审查，最后合并。corral 程序本身不引用它。

- 安装：把这个目录软链接到 `~/.claude/skills/corral-dispatch` 和 `~/.agents/skills/corral-dispatch`。
- 在项目里启用：把 [`项目AGENTS模板.md`](corral-dispatch-skill/项目AGENTS模板.md) 里那一节贴进项目的 `AGENTS.md`。项目里不用写哪家做什么、用哪一档，由技能决定。
- 交给哪家：主控照技能里的一张分工表定（Codex 做后端，Claude Code 做前端，pi 做项目代码之外的轻档杂活）。
- 几档（轻、常规、重对应的模型和强度）、要不要交叉审查、影响面（看得见、改行为、碰要害，决定验证和审查做多少）：由 [`route.py`](corral-dispatch-skill/route.py) 把一段任务摘要交给分类模型（TypeSafe）判断。有把握的结论照用；拿不准、没有 `TYPESAFE_API_KEY`，或者调用失败，都退回主控自己判断，分派不会被卡住。
- 费 token 的配置一律先问用户。

详见 [`corral-dispatch-skill/README.md`](corral-dispatch-skill/README.md)。

## agent 升级之后

各家 agent 改钩子不会事先通知。任何一家升级后，跑一遍冒烟：

```sh
<仓库>/tools/smoke                   # 四家都跑，约 8 分钟，花少量 token
<仓库>/tools/smoke --agents claude   # 只跑一家
```

它用单独的短路径状态目录和临时目录，不碰你正在用的 agent；只核对机制（启动、送达、工具状态、打断、停止、委派），不核对模型回答的内容。

## 规矩

- 所有 agent 都用 corral 开，主对话也不例外。
- 一个 agent 只走一条通道：corral 开的 agent，只通过 corral 和它说话。
- 只碰自己开的 agent，不给别人开的送话，也不 stop 它们。
- 不读状态目录（`~/.corral`），只看命令输出。
- corral 不会替 agent 回答对话框。`blocked` 就是该人接进去决定了。

## 不做

前端界面、多窗格布局、终端模拟、会话恢复、远程机器、Windows。

## 文档

- [docs/USAGE.md](docs/USAGE.md)：使用指南
- [docs/CONTRACT.md](docs/CONTRACT.md)：对外契约（命令、字段、退出码）
- [docs/DESIGN.md](docs/DESIGN.md)：设计和背后的理由
- [docs/ROADMAP.md](docs/ROADMAP.md)：路线图和冒烟记录
- [docs/SPIKE.md](docs/SPIKE.md)：试验和结果
- `corral guide`：给 agent 看的使用说明

## 开发

```sh
python3 -m unittest discover -s tests -t .   # 全量测试，约 3.5 分钟
python3 -m unittest tests.test_skills        # 单个模块
```

- 只用标准库，不加依赖。
- 改栏位、协议、钩子或事件格式风险最高：要有兼容方案、上一个版本的兼容测试，并用真实 agent 跑一遍。
- 加功能前先判断放在哪一层（内核、技能，还是本仓库之外的 harness），见 [docs/DESIGN.md](docs/DESIGN.md) 第 16 节。
