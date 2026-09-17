"""假 agent：按 Claude Code / Codex 的方式读启动参数里的钩子并执行，在伪终端里收输入、按剧本产生事件。

种类由程序名决定（support.fake_agent 生成名为 claude / codex / pi / omp 的包装脚本）。
pi、omp 不跑命令钩子：真 agent 加载 --extension 指定的 TypeScript 扩展，由扩展直接写事件文件。假 agent 按
hook_pi.ts / hook_omp.ts 的映射直接写同样格式的事件（扩展本身由 tests/test_pi_hook.py、test_omp_hook.py 单独测）。

提交的文字决定这一轮做什么：
  reply:<文字>     转圈 0.3 秒后回合结束，回复 <文字>
  tool:<秒>        调用 Bash 工具，转圈 <秒> 后结束
  ask              提问：权限请求事件后等人按 1
  hang             进入 working 后既不输出也不结束（模拟打断后没有事件的情况）
  slow:<秒>        转圈 <秒> 后结束
  subagent         主会话结束后，一个内部子会话继续产生事件（Codex 记忆整理那种）
  selfturn         回合结束后 0.5 秒自己注入一条输入再跑一轮
  menu             回合结束后打开一个「菜单」：之后的文字和回车都被菜单吞掉，不提交
  其他             回复 "echo: <文字>"

环境变量（用 corral start --env 传）：
  FAKE_LOG         日志文件：启动参数、钩子的标准输出
  FAKE_SCRIPT      JSON 字符串列表：启动后依次自动提交，每条等上一轮结束
  FAKE_SWAP_FIRST  1 = Codex 第一次提交时先写输入事件、再写会话开始事件
  FAKE_IGNORE_CTRL_C  1 = 不理 Ctrl-C（测 stop 升级到信号）
  FAKE_IGNORE_PROMPT  1 = 启动参数里的首句不提交（测「首句没提交时不能报 idle」）
  FAKE_SHUTDOWN_DELAY 秒数：连按两次 Ctrl-C 后先「收尾」这么久再退出（真 Codex 实测要好几秒）
"""
import json
import os
import select
import signal
import subprocess
import sys
import termios
import time
import tomllib
import tty
import uuid

FLAVOR = globals().get("FAKE_FLAVOR") or os.path.basename(sys.argv[0])
CODEX = FLAVOR == "codex"
EXT = FLAVOR in ("pi", "omp")  # 靠 --extension 扩展直接写事件的 agent
SPIN = "|/-\\"


def log(obj):
    path = os.environ.get("FAKE_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(obj) + "\n")


def parse_args(args):
    hooks, bypass, positional = {}, False, []
    extension = None
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--settings":
            for ev, entries in json.loads(args[i]).get("hooks", {}).items():
                hooks.setdefault(ev, []).extend(h["command"] for e in entries for h in e["hooks"])
            i += 1
        elif a == "-c":
            value = args[i]
            i += 1
            if value.startswith("hooks."):
                for ev, entries in tomllib.loads(value)["hooks"].items():
                    hooks.setdefault(ev, []).extend(h["command"] for e in entries for h in e["hooks"])
        elif a == "--dangerously-bypass-hook-trust":
            bypass = True
        elif a == "--extension":
            extension = args[i]
            i += 1
        elif a in ("-m", "--model"):
            i += 1
        elif a == "--allowedTools":
            # 和真 Claude Code 一样是多值选项：吞掉后面所有不以 - 开头的参数
            while i < len(args) and not args[i].startswith("-"):
                i += 1
        elif a == "--":
            positional.extend(args[i:])
            break
        elif not a.startswith("-"):
            positional.append(a)
    return hooks, bypass, (positional[-1] if positional else None), extension


class Agent:
    def __init__(self):
        self.hooks, bypass, self.first_prompt, self.extension = parse_args(sys.argv[1:])
        log({"argv": sys.argv})
        self.hooks_enabled = not (CODEX and self.hooks and not bypass)
        if EXT:  # 扩展副本必须真的在栏位目录里，才算挂上了
            self.hooks_enabled = bool(self.extension and os.path.isfile(self.extension))
        self.sid = uuid.uuid4().hex
        self.session_started = False
        self.buffer = b""
        self.in_paste = False
        self.job = []           # [(时间, 动作)]
        self.working = False
        self.spinning = False
        self.waiting_answer = False
        self.script = json.loads(os.environ.get("FAKE_SCRIPT", "[]"))
        self.ctrl_c_at = 0.0
        self.menu_open = False

    # ---- 钩子

    def fire(self, event, sid=None, **fields):
        if not self.hooks_enabled:
            return
        if EXT:
            self.fire_pi(event, **fields)
            return
        payload = {"session_id": sid or self.sid, "cwd": os.getcwd(), "hook_event_name": event,
                   "transcript_path": f"/tmp/fake-{sid or self.sid}.jsonl", **fields}
        if sid and sid != self.sid:
            payload["transcript_path"] = None
            payload["cwd"] = "/tmp"
        for cmd in self.hooks.get(event, []):
            proc = subprocess.run(cmd, shell=True, input=json.dumps(payload).encode(), capture_output=True,
                                  timeout=15)
            if proc.stdout:
                log({"hook_stdout": proc.stdout.decode("utf-8", "replace"), "event": event})

    def out(self, s):
        os.write(1, s.encode() if isinstance(s, str) else s)

    # ---- 一轮

    def submit(self, text):
        if CODEX and not self.session_started:
            self.session_started = True
            if os.environ.get("FAKE_SWAP_FIRST") == "1":
                self.fire("UserPromptSubmit", prompt=text)
                self.fire("SessionStart", source="startup")
            else:
                self.fire("SessionStart", source="startup")
                self.fire("UserPromptSubmit", prompt=text)
        else:
            self.fire("UserPromptSubmit", prompt=text)
        self.out(f"\r\n[you] {text}\r\n")
        self.working, self.spinning = True, True
        now = time.time()
        cmd, _, arg = text.partition(":")
        if cmd == "reply":
            self.job = [(now + 0.3, ("stop", arg))]
        elif cmd == "tool":
            self.job = [(now, ("fire", "PreToolUse", {"tool_name": "Bash"})),
                        (now + float(arg), ("fire", "PostToolUse", {"tool_name": "Bash"})),
                        (now + float(arg), ("stop", "tool done"))]
        elif cmd == "ask":
            self.job = [(now, ("fire", "PreToolUse", {"tool_name": "AskUserQuestion"})),
                        (now, ("fire", "PermissionRequest", {"tool_name": "AskUserQuestion"})),
                        (now, ("ask",))]
        elif cmd == "hang":
            self.spinning = False
            self.job = []
        elif cmd == "slow":
            self.job = [(now + float(arg), ("stop", "slow done"))]
        elif cmd == "subagent":
            sub = uuid.uuid4().hex
            self.job = [(now + 0.1, ("fire_sub", sub, "SessionStart", {"source": "startup"})),
                        (now + 0.2, ("fire_sub", sub, "UserPromptSubmit", {"prompt": "consolidate"})),
                        (now + 0.3, ("stop", "main done")),
                        (now + 0.6, ("fire_sub", sub, "PreToolUse", {"tool_name": "Bash"}))]
        elif cmd == "menu":
            self.job = [(now + 0.2, ("stop", "menu opened")), (now + 0.2, ("menu",))]
        elif cmd == "selfturn":
            self.job = [(now + 0.2, ("stop", "waiting for background task")),
                        (now + 0.7, ("inject", "<task-notification>done</task-notification>"))]
        else:
            self.job = [(now + 0.2, ("stop", f"echo: {text}"))]

    def run_job(self):
        now = time.time()
        while self.job and self.job[0][0] <= now:
            _, action = self.job.pop(0)
            kind = action[0]
            if kind == "fire":
                self.fire(action[1], **action[2])
            elif kind == "fire_sub":
                self.fire(action[2], sid=action[1], **action[3])
            elif kind == "stop":
                self.fire("Stop", last_assistant_message=action[1])
                self.out(f"\r\n[agent] {action[1]}\r\n> ")
                self.working = self.spinning = False
            elif kind == "ask":
                self.spinning = False
                self.waiting_answer = True
                self.out("\r\n? pick 1\r\n")
            elif kind == "menu":
                self.menu_open = True
            elif kind == "inject":
                self.submit(action[1])
                self.job.append((time.time() + 0.3, ("stop", "final")))
                return

    # ---- 输入

    def on_input(self, data):
        log({"input": data.decode("utf-8", "replace")})
        i = 0
        while i < len(data):
            if data.startswith(b"\x1b[200~", i):
                self.in_paste, i = True, i + 6
                continue
            if data.startswith(b"\x1b[201~", i):
                self.in_paste, i = False, i + 6
                continue
            if self.menu_open:
                i += 1
                continue
            if data.startswith(b"\x1b[", i) and not self.in_paste:
                j = i + 2  # 跳过整个 CSI 序列（方向键、焦点事件、鼠标等），不进输入框
                while j < len(data) and not (0x40 <= data[j] <= 0x7e):
                    j += 1
                i = j + 1
                continue
            ch = data[i:i + 1]
            i += 1
            if self.in_paste:
                self.buffer += ch
                continue
            if ch == b"\x1b" and not data.startswith(b"[", i):
                self.interrupt()
            elif ch == b"\x03":
                self.ctrl_c()
            elif ch == b"\r":
                if not self.working and self.buffer:
                    text, self.buffer = self.buffer.decode("utf-8", "replace"), b""
                    self.submit(text)
            elif ch == b"1" and self.waiting_answer:
                self.waiting_answer = False
                self.spinning = True
                now = time.time()
                self.job = [(now, ("fire", "PostToolUse", {"tool_name": "AskUserQuestion"})),
                            (now + 0.1, ("stop", "answered"))]
            elif ch >= b" ":
                self.buffer += ch
                self.out(ch)

    def fire_pi(self, event, **fields):
        """按 hook_pi.ts 的映射写事件：权限请求在 pi 里是扩展弹框（通知），打断后 agent_settled 记为回合结束。"""
        if event == "PermissionRequest":
            event, fields = "Notification", {"notification_type": "permission_prompt"}
        path = os.environ.get("CORRAL_EVENTS")
        if not path:
            return
        rec = {"v": 1, "t": time.time(), "ev": event, "inst": os.environ.get("CORRAL_INSTANCE", ""),
               "has_transcript": True, "session_id": self.sid, "cwd": os.getcwd()}
        rec.update({k: v for k, v in fields.items() if isinstance(v, (str, int, float, bool))})
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def interrupt(self):
        if not self.working:
            return
        self.job, self.spinning, self.waiting_answer = [], False, False
        if CODEX:
            self.working = False
            self.fire("Interrupt")
        if EXT:  # 按 hook_pi.ts：打断后 agent_settled 照常触发，记为回合结束（真 pi 待实测）
            self.working = False
            self.fire("Stop")
        # Claude Code 被打断时没有任何事件，状态停在 working；working 标志留着，界面不再输出

    def ctrl_c(self):
        if os.environ.get("FAKE_IGNORE_CTRL_C") == "1":
            return
        now = time.time()
        if now - self.ctrl_c_at < 2.0:
            log({"quit": "ctrl-c twice"})
            self.out("\r\nShutting down...\r\n")
            time.sleep(float(os.environ.get("FAKE_SHUTDOWN_DELAY", "0")))
            sys.exit(0)
        self.ctrl_c_at = now
        self.working, self.spinning, self.job = False, False, []
        self.out("\r\n(press ctrl-c again to quit)\r\n")

    # ---- 主循环

    def main(self):
        if CODEX:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)  # 真 Codex 不理 SIGHUP
        if EXT:  # 真 pi 收到 SIGHUP 触发 session_shutdown 后退出
            signal.signal(signal.SIGHUP, lambda *_: (self.fire("SessionEnd"), sys.exit(0)))
        attrs = termios.tcgetattr(0)
        tty.setraw(0)
        try:
            self.out(f"\x1b[?2004h\x1b]0;fake-{FLAVOR}\x07fake {FLAVOR}\r\n> ")
            if not CODEX:
                self.fire("SessionStart", source="startup")
                self.session_started = True
            if self.first_prompt is not None and os.environ.get("FAKE_IGNORE_PROMPT") != "1":
                self.submit(self.first_prompt)
            frame = 0
            while True:
                r, _, _ = select.select([0], [], [], 0.1)
                if r:
                    data = os.read(0, 65536)
                    if not data:
                        return
                    self.on_input(data)
                self.run_job()
                if self.spinning:
                    frame += 1
                    self.out(f"\r{SPIN[frame % 4]} working")
                if not self.working and self.script:
                    self.submit(self.script.pop(0))
        finally:
            termios.tcsetattr(0, termios.TCSADRAIN, attrs)


if __name__ == "__main__":
    Agent().main()
