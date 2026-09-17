# corral 使用说明（给 agent 看）

corral 让你开启别的交互式编程 agent（Claude Code、Codex），给它送话、等它答完、取回复。每个 agent 有一个名字（如 `demo/ask-3`），用名字操作它。人随时可以在自己的终端里 `corral attach <名字>` 进去看、插话。

所有命令输出一行 JSON，看 `ok` 和退出码。完整契约见 corral 仓库的 docs/CONTRACT.md。**不要读 corral 状态目录里的任何文件。**

**你自己也必须是用 corral 启动的。** 环境变量 `CORRAL_NAME` 就是你自己的名字；它为空时不要委派，告诉人先用 `corral start` 启动你、再 `corral attach` 进来。

## 临时委派：开一个 agent，问一个问题

```sh
corral start demo/ask --unique --cwd <仓库目录> --prompt "<你的问题>" -- claude
corral wait <名字>
corral reply <名字>
```

- `start` 输出里的 `name` 就是之后要用的名字（`--unique` 会补后缀，如 `demo/ask-3`）。
- 第一句话**必须**用 `--prompt` 带上；新开的 agent 不能用 `send` 送第一句。
- 换成 Codex：`-- codex --yolo`（在 Codex 沙箱里 corral 会直接拒绝，见下）。换成 pi：`-- pi`。
- `reply` 的 `text` 是回复原文，多段文字和代码块原样保留。

追问、用完：

```sh
corral send <名字> "<追问>"
corral wait <名字>
corral reply <名字>
corral stop <名字>
```

对方不会自己退出，你也**不要自己关**：人可能还要接着让它做。把结果告诉人时说明它还开着、名字是什么；人说关掉时才 `stop`。`corral ls` 看还开着哪些。

**一个 agent 只走一条通道**：用 corral 开的 agent 仍然是一个普通的 agent 会话，别的渠道（会话之间的消息、子 agent 工具）也能碰到它，但 corral 看不见那些输入。送话、等待、停止都只用 corral，不要混用；否则状态和「最近一次输入的来源」会失真，`wait` 和 `reply` 会对不上。

## 长任务：交出去不等，做完被提醒

要跑很久，或者人说「做完告诉我」，就不要在前台 `wait`：

```sh
corral start demo/ask --unique --cwd <仓库目录> --prompt "<你的问题>" -- codex --yolo
corral send "$CORRAL_NAME" "<提醒的话>" --after <名字> --timeout 3600
```

- 交给对方的话末尾加上「命令都在前台跑完，全部做完后，回复最后一行写 DONE」。
- 提醒的话写成「<名字> 这一轮结束了，去看它的状态和回复」。`send --after` 立即返回；告诉人已经交出去，然后结束这一轮。
- 对方这一轮结束（或卡在对话框、退出、等满 `--timeout`）时，这句话会像人打字一样送进你的输入框；你正在干活、人刚在你的窗口里操作过时，它会等一会儿再送。
- 收到后 `corral status <名字>`、`corral reply <名字>`：回复最后一行是 DONE，取结果告诉人，不要 stop；对方又在干活，是被叫早了，再挂一次 `send --after`；没有 DONE 但对方已经停了，告诉人去 attach 看。
- corral 只知道「这一轮结束」，不知道「做完」，所以一定要靠 DONE 判断。
- 让已经开着的对方接着做长活：用 `send` 把新任务交过去（末尾同样带 DONE 约定），再挂 `send --after`，不要重新 start。

## 等待时要知道的

- `wait` 返回的 `result`：`idle` 这一轮结束；`blocked` 弹了权限框或提问框，要人处理（告诉人去 `corral attach <名字>`）；`stopped-quiet` 只有加了 `--quiet 秒` 才会出现，表示 agent 在 working 状态下长时间没动静，多半是人在窗口里打断了它。
- `idle` 只表示这一轮结束，agent 之后可能自己再开一轮。拿到 `reply` 后如果 `last_input_source` 是 `agent`，说明这一轮是它自己开的。
- `wait` 默认最多等 600 秒，可用 `--timeout` 改；超时退出码 4。你的 shell 工具如果有超时（常见是两分钟），就用 `--timeout 90` 反复调用，直到返回结果。状态一直是 `starting`，说明它启动时卡在对话框里，让人接入去看。

## send 被拒绝时

| 退出码 | 原因 | 怎么办 |
|---|---|---|
| 7 `not_idle` | agent 还在忙或还没启动好 | 先 `wait` 再 `send` |
| 8 `human_active` | 人刚在接入窗口里操作过 | 过一会儿再试；确定要打断人才加 `--force` |
| 3 `not_delivered` | 送了但 agent 没收到（屏幕上可能是菜单或对话框） | 不要盲目重试回车；`corral status` 看状态，必要时让人接入去看 |

送成功时如果 `merged_with_draft` 是 `true`，说明输入框里原来有人留下没提交的文字，你的话接在它后面一起提交了；agent 收到了，**不要重送**，在意的话让人接入去看。

## 其他命令

```sh
corral status <名字>
corral keys <名字> esc
corral ls
corral where <名字>
corral attach --wait <名字>
```

- `keys` 送原始按键（`enter` `esc` `up` `down` `ctrl-c`，或 `text:<文字>`），不看状态，后果自负；`esc` 可以打断正在干活的 agent。按完键 agent 要过一会儿才反应，紧接着 `send` 可能因为状态还没变被退回 7，先 `status` 或 `wait` 再送。`keys` 不算人在打字，不会触发 8。
- `read` 给的是**累积的输出**（最近若干字节的滚动缓冲），不是当前画面：启动横幅、早已过去的提示都还在里面，拿整段做子串匹配会被历史内容骗到。要判断「现在屏幕上是什么」只看尾部（最后几百字符），并且只作排查，不要靠它做决定。
- 退出码 2：不存在或已退出；6：你在 Codex 沙箱里，corral 用不了，需要调用方不带沙箱启动；9：版本不兼容，`stop` 后重新 `start`。
