---
name: corral
description: "Start a separate, new Claude Code or Codex agent session with the corral command, hand it the question, wait for its answer and bring back its reply. Use whenever the user asks to open, start or spin up another agent to look at something, e.g. 开一个 Claude Code 看一下, 开一个 Codex 看一下, 交给另一个 agent, 让另一个 agent 帮忙看看, ask another agent, delegate to another agent, get a second opinion from Claude Code or Codex. The user wants a separate interactive agent session they can attach to, not a built-in subagent or Agent/Task tool, and not your own answer, even if you are Claude Code or Codex yourself."
---
<!-- corral-skill: written by `corral install-skills`; `corral install-skills --remove` deletes it -->

# corral：开另一个 agent，送话，取回复

先自检，两项都要过：
1. 运行 `command -v corral`。没有输出，就告诉用户「corral 没装（不在 PATH 上）」，然后停下，不要自己去找或安装。
2. 运行 `echo "$CORRAL_NAME"`。输出为空，说明你自己不是用 corral 启动的。按约定所有 agent 都要在 corral 里，告诉用户「请先用 `corral start <名字> --cwd <目录> -- <agent 命令>` 启动我，再 `corral attach <名字>` 进来」，然后停下，不要委派。

## 标准四步

```sh
corral start demo/ask --unique --cwd <工作目录> --prompt "<要交给对方的话>" -- claude
corral wait <名字> --timeout 90 --quiet 120
corral reply <名字>
corral stop <名字>
```

1. **start**：输出里的 `name` 就是之后用的名字（`--unique` 会补后缀）。第一句话必须用 `--prompt` 带上。要开 Codex，把最后换成 `-- codex --yolo`。需要省钱时，调用方可以自己在 agent 命令后面传模型参数。
2. **wait**：你的 shell 工具有超时，所以每次只等 90 秒；退出码 4 就再运行一次，直到返回。看输出里的 `result`：
   - `idle`：这一轮结束，去 reply。
   - `blocked`：对方弹了权限框或提问框。不要替它回答，告诉用户运行 `corral attach <名字>` 去处理，处理完再 wait。
   - `stopped-quiet`：对方 120 秒没有动静，多半被人打断了。告诉用户，不要当成已经答完。
3. **reply**：`text` 是对方回复的原文，整理后告诉用户。
4. **stop**：用完一定要 stop。需要追问就先 `corral send <名字> "<追问>"`，再 wait、reply，最后 stop。

## 长任务：交出去不等，做完被提醒

要跑很久（跑测试、批量改、同时开好几个），或者用户说「做完告诉我」，就不要在前台 wait：

```sh
corral start demo/ask --unique --cwd <工作目录> --prompt "<要交给对方的话>" -- claude
corral send "$CORRAL_NAME" "<提醒的话>" --after <名字> --timeout 3600
```

1. 交给对方的话末尾固定加上：「命令都在前台跑完，全部做完后，回复最后一行写 DONE」。
2. 提醒的话写成「<名字> 这一轮结束了，去看它的状态和回复」。`send --after` 立即返回，告诉用户已经交出去，然后结束这一轮。
3. 对方这一轮结束时，这句话会送进你的输入框。收到后运行 `corral status <名字>` 和 `corral reply <名字>`：
   - 回复最后一行是 DONE：整理结果告诉用户，然后 stop。
   - 状态是 working：被叫早了，再运行一次上面的 `send --after`，结束这一轮。
   - 空闲、blocked 或已经不在，但没有 DONE：告诉用户去 `corral attach <名字>` 看。

## 退出码

| 退出码 | 标识 | 怎么办 |
|---|---|---|
| 2 | `not_found` | 对方已经不在了，需要的话重新 start |
| 3 | `not_delivered` | 对方没收到。不要重试回车；运行 `corral status <名字>` 看状态，必要时请用户接入去看 |
| 4 | `timeout` | wait 还没等到，再运行一次 wait |
| 6 | `sandbox` | 你在 Codex 沙箱里，corral 用不了。告诉用户不带沙箱重启你（例如 `codex --yolo`） |
| 7 | `not_idle` | 对方还在忙，先 wait 再 send |
| 8 | `human_active` | 用户刚在对方的窗口里操作过，过一会儿再试，不要加 `--force` |

## 规矩

- 不替对方回答对话框（不要用 `corral keys` 去点确认），交给用户。
- 只给你自己 start 的 agent 送话，不碰 `corral ls` 里别人开的。
- **一个 agent 只走一条通道**：用 corral 开的 agent，送话、等待、停止都只用 corral，不要再用别的渠道（会话之间的消息、子 agent 工具）给同一个 agent 送话；否则 corral 看到的状态和输入来源会失真，wait 和 reply 会对不上。
- 名字由你起：用有意义的前缀加 `--unique`。
- 不要读 corral 状态目录里的文件，只看命令输出的 JSON。
- 更多命令和细节：运行 `corral guide`。
