# corral 使用指南（给人看）

这份是日常用的说明：怎么开 agent、怎么从不同终端接进去、怎么让几个 agent 配合。命令的精确字段和退出码以 [CONTRACT.md](CONTRACT.md) 为准；给 agent 看的那份是 `corral guide`；设计理由在 [DESIGN.md](DESIGN.md)。

## 1. 先记住的模型

- **一个 agent 一个栏位**，用名字操作，如 `demo/alice`。名字用 `/` 分段，按项目加前缀。
- **窗口只是观看者**。agent 由栏位托着，在哪个终端里看、看不看、关掉窗口，都不影响它跑。
- **状态来自 agent 自己的钩子**，不读屏。所以 corral 知道它是 idle、working 还是 blocked，但不知道屏幕上现在画的是输入框还是菜单。
- **实例编号**：同名 agent 退出后再开，名字一样、实例编号不同。脚本要核对它，别把话塞给一个毫不知情的新对话。
- corral 只提供机制。「开谁、送什么、空闲后做什么」这些判断写在你自己的脚本里。

## 2. 安装与检查

```sh
ln -s <仓库>/bin/corral ~/.local/bin/corral   # 软链即可，仓库更新自动生效
corral --version                               # {"ok": true, "version": "…", "contract": "1"}
corral install-skills                          # 把 skill 装进 Claude Code 和 Codex，会先列路径再问 y/N
```

skill 装好后，任何 Claude Code 或 Codex 会话里说「开一个 Codex 看一下」「交给另一个 agent」，它就会自己用 corral 去开。SKILL.md 改了才需要重装。

所有命令输出一行 JSON，看 `ok` 和退出码。缩进看的话接 `| python3 -m json.tool`。

## 3. 单个 agent 的一生

### 开

```sh
corral start demo/alice --cwd ~/proj -- claude
corral start demo/alice --cwd ~/proj -- codex --yolo
corral start demo/ask --unique --cwd ~/proj --prompt "看一下这个想法：……" -- claude
```

- `--` 后面是 agent 的完整命令，模型等参数照常跟在后面，如 `-- claude --model haiku`。
- Codex 一律 `--yolo`：它默认开沙箱，沙箱里连不上 corral，也写不了仓库外的文件。
- `--prompt` 是第一句话，由 agent 自己提交。**新开的 agent 送第一句只能走这里**，Codex 在第一次提交前没有任何事件，`send` 会被拒。
- `--unique` 把名字当前缀，自动补后缀（`demo/ask-3`），以输出里的 `name` 为准。要同时开好几个就用它。
- `--env KEY=VALUE` 给 agent 补环境变量。agent 的环境取自你的登录 shell，不是当前终端的。
- 同名已在跑：退出码 5。启动后卡在信任框等对话框：状态一直是 `starting`，接进去处理。

### 看、插话

```sh
corral attach demo/alice          # 接进去，按 Ctrl-] 退出，agent 继续跑
corral attach --wait demo/alice   # 名字还不存在就等着，一出现自动接上；agent 退出后回到等待
```

- 第一个接入的窗口能打字，之后的只读；终端尺寸跟着能打字的那个走。
- 在任意终端软件、任意标签里都能接，接完退出，换个终端再接，agent 不受影响。
- 「让它自动出现在旁边」：先开个分屏挂 `attach --wait`，再 start。分屏由你的终端软件做，corral 不管布局。
- 人在接入窗口里操作过之后的 30 秒内，脚本的 `send` 会避让（退出码 8）。只看不动不影响。

### 查

```sh
corral status demo/alice   # state、last_tool、turn_started、last_input_source、attached、idle_for…
corral ls                  # 所有活着的 agent，顺手清掉已死的残留
corral where demo/alice    # cwd、agent_pid
corral read demo/alice     # 最近的原始输出，只作排查
```

状态值：`starting` 启动中或卡在对话框 / `idle` 这一轮结束 / `working` / `blocked` 弹了权限框或提问框 / `exiting` / `unknown` 不认识的 agent。

`idle` 只表示这一轮结束，agent 可能自己再开一轮（后台命令结束后注入通知就是这样）。看 `last_input_source` 是 `send`、`human` 还是 `agent`。

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

临时 agent 不会自己退出，用完记得 stop；`corral ls` 看还开着哪些。

## 4. 多 agent 怎么配合

下面每种都在真实 agent 上验证过。名字自己起，示例用 `demo/` 前缀。

### 4.1 临时委派：开一个问一句

```sh
name=$(corral start demo/ask --unique --cwd ~/proj --prompt "请评审 src/parse.py 的错误处理" -- codex --yolo \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
corral wait "$name" --timeout 600
corral reply "$name"
corral send "$name" "第二点再展开说说"      # 追问
corral wait "$name" --timeout 600 && corral reply "$name"
corral stop "$name"
```

固定名字则是「接着上次的对话问」，`--unique` 是「每次一个干净的新对话」。装了 skill 的 agent 会自己走这四步。

### 4.2 常驻工作 agent + 叫醒脚本

一个 agent 一直开着干活，脚本在它空闲时给它送下一件事：

```sh
#!/bin/sh
# wake <名字> <话>：等到 idle 再送；实例变了就不送
name=$1; text=$2
inst() { corral status "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"])'; }
want=$(inst "$name") || exit 2
while :; do
  [ "$(inst "$name")" = "$want" ] || { echo instance_changed; exit 3; }
  corral send "$name" "$text"; code=$?
  case $code in
    0) exit 0 ;;
    7|8) sleep 5 ;;      # 还在忙 / 人刚操作过：稍后再试
    *) exit $code ;;     # 3 没送到、2 不在了：交给人
  esac
done
```

要点：先记实例编号；7 和 8 是「等一等」，3 不要盲目重试。

### 4.3 请求 / 交付：靠文件交接

A 把请求写进文件，脚本开 B 去做，B 写结果文件并在最后一行放哨兵，脚本等 B 结束后叫醒 A：

```sh
corral start demo/review --cwd ~/proj-wt --prompt "读 request.md，把结论写进 findings.md，最后一行写 DONE" -- codex --yolo
corral wait demo/review --timeout 900
tail -1 ~/proj-wt/findings.md | grep -q '^DONE$' \
  && corral send demo/alice "findings.md 已交付，请处理" \
  || echo undelivered
```

- B 的工作目录可以是同一仓库的 worktree，两边互不干扰。
- 要验的要求写进送出去的那句话里，写在文件里的附加要求容易被忽略。
- B 可以固定名字反复复用（下一轮 `send` 新请求），也可以每轮 stop 再 start。预先挂着的 `attach --wait` 会在每次 start 时自动接上。
- 交付判定靠哨兵行，别靠 idle：idle 只说明这一轮结束，不说明做完了。

### 4.4 同时开几个

`--unique` 起几个就是几个，各自独立，互不串扰。不同项目用不同前缀（`proj-a/…`、`proj-b/…`），`corral ls` 一眼分清。

## 5. 写脚本时的规矩

- **一个 agent 只走一条通道**。用 corral 开的 agent，送话、等待、停止都只用 corral。它仍然是普通会话，别的渠道（会话之间的消息、子 agent 工具）也能碰到它，但 corral 看不见那些输入，混用后状态和输入来源都会失真。
- **只碰自己开的**。`corral ls` 里别人开的不要送话，更不要 stop。
- **不读状态目录**（`~/.corral`）里的任何文件，只看命令输出。内部格式随时会改。
- **不替 agent 回答对话框**，`blocked` 交给人接入处理。
- `read` 是累积输出不是当前画面，判断屏幕只看尾部，而且只作排查，不要靠它做决定。

退出码速查：

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

## 6. 出岔子

- **一直 `starting`**：卡在信任框或别的启动对话框里，接进去处理；Codex 在第一句提交前也是 starting，所以第一句必须用 `--prompt`。
- **wait 返回 `blocked`**：权限框或提问框，接进去点；处理完状态会回到 `working`，再 wait。
- **`stopped-quiet`**：人打断了它。看窗口决定是重送还是作罢。
- **send 退 3**：屏幕上可能是菜单或对话框把文字吞了。corral 不补发按键，因为补发的回车可能点中菜单项。接进去看。
- **stop 出来 `stopped_by: SIGTERM`**：Codex 没在 60 秒内收尾完，会话里未落盘的东西可能丢了。记下来，这是要放宽阈值的信号。
- **退 9**：新旧版本的 corral 混着用了。用启动它的那个版本 stop，再用新版 start。
- **整个终端软件退了**：agent 照常在跑，`corral ls` 找回来再 attach。

## 7. 边界

- 状态目录默认 `~/.corral`，环境变量 `CORRAL_HOME` 可改；同一台机器、同一用户看到的是同一个目录。agent 环境里带着 `CORRAL_HOME`、`CORRAL_NAME`、`CORRAL_INSTANCE`，agent 里再调 corral 看到的也是同一份。
- 钩子只随 corral 启动的那个 agent 生效，不写任何全局配置。你自己的全局钩子和插件照常对它生效。
- 不做的：前端、多窗格布局、终端模拟、会话恢复、远程机器、Windows。
