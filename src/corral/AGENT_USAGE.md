# corral 使用说明（给 agent 看）

corral 让你开启别的交互式编程 agent（Claude Code、Codex），给它送话、等它答完、取回复。每个 agent 有一个名字（如 `demo/ask-3`），用名字操作它。人随时可以在自己的终端里 `corral attach <名字>` 进去看、插话。

所有命令输出一行 JSON，看 `ok` 和退出码。完整契约见 corral 仓库的 docs/CONTRACT.md。**不要读 corral 状态目录里的任何文件。**

## 临时委派：开一个 agent，问一个问题

```sh
corral start demo/ask --unique --cwd <仓库目录> --prompt "<你的问题>" -- claude
corral wait <名字>
corral reply <名字>
```

- `start` 输出里的 `name` 就是之后要用的名字（`--unique` 会补后缀，如 `demo/ask-3`）。
- 第一句话**必须**用 `--prompt` 带上；新开的 agent 不能用 `send` 送第一句。
- 换成 Codex：`-- codex --yolo`（在 Codex 沙箱里 corral 会直接拒绝，见下）。
- `reply` 的 `text` 是回复原文，多段文字和代码块原样保留。

追问、用完：

```sh
corral send <名字> "<追问>"
corral wait <名字>
corral reply <名字>
corral stop <名字>
```

临时 agent 不会自己退出，用完记得 `stop`；`corral ls` 看还开着哪些。

## 等待时要知道的

- `wait` 返回的 `result`：`idle` 这一轮结束；`blocked` 弹了权限框或提问框，要人处理（告诉人去 `corral attach <名字>`）；`stopped-quiet` 只有加了 `--quiet 秒` 才会出现，表示 agent 在 working 状态下长时间没动静，多半是人在窗口里打断了它。
- `idle` 只表示这一轮结束，agent 之后可能自己再开一轮。拿到 `reply` 后如果 `last_input_source` 是 `agent`，说明这一轮是它自己开的。
- `wait` 默认最多等 600 秒，可用 `--timeout` 改；超时退出码 4。状态一直是 `starting`，说明它启动时卡在对话框里，让人接入去看。

## send 被拒绝时

| 退出码 | 原因 | 怎么办 |
|---|---|---|
| 7 `not_idle` | agent 还在忙或还没启动好 | 先 `wait` 再 `send` |
| 8 `human_active` | 人刚在接入窗口里操作过 | 过一会儿再试；确定要打断人才加 `--force` |
| 3 `not_delivered` | 送了但 agent 没收到（屏幕上可能是菜单或对话框） | 不要盲目重试回车；`corral status` 看状态，必要时让人接入去看 |

## 其他命令

```sh
corral status <名字>
corral keys <名字> esc
corral ls
corral where <名字>
corral attach --wait <名字>
```

- `keys` 送原始按键（`enter` `esc` `up` `down` `ctrl-c`，或 `text:<文字>`），不看状态，后果自负；`esc` 可以打断正在干活的 agent。
- 退出码 2：不存在或已退出；6：你在 Codex 沙箱里，corral 用不了，需要调用方不带沙箱启动；9：版本不兼容，`stop` 后重新 `start`。
