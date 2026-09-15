---
name: corral
description: "Start a separate, new Claude Code or Codex agent session with the corral command, hand it the question, wait for its answer and bring back its reply. Use whenever the user asks to open, start or spin up another agent to look at something, e.g. 开一个 Claude Code 看一下, 开一个 Codex 看一下, 交给另一个 agent, 让另一个 agent 帮忙看看, ask another agent, delegate to another agent, get a second opinion from Claude Code or Codex. The user wants a separate interactive agent session they can attach to, not a built-in subagent or Agent/Task tool, and not your own answer, even if you are Claude Code or Codex yourself."
---
<!-- corral-skill: written by `corral install-skills`; `corral install-skills --remove` deletes it -->

# corral：开另一个 agent，送话，取回复

先自检：运行 `command -v corral`。没有输出，就告诉用户「corral 没装（不在 PATH 上）」，然后停下，不要自己去找或安装。

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
- 名字由你起：用有意义的前缀加 `--unique`。
- 不要读 corral 状态目录里的文件，只看命令输出的 JSON。
- 更多命令和细节：运行 `corral guide`。
