"""配套工具 tools/board：只读公开输出的终端看板，面板加显示器。"""
import json
import os
import pty
import re
import subprocess
import sys
import time
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase
from tests.test_attach import Window

BOARD = os.path.join(support.ROOT, "tools", "board")


class BoardTest(AgentTestCase):
    def board(self, *args):
        env = dict(os.environ, CORRAL_HOME=self.home, SHELL=support.fast_shell())
        env.pop("CODEX_SANDBOX", None)
        proc = subprocess.run([sys.executable, BOARD, "--once", *args], capture_output=True, text=True,
                              env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def row(self, out, name):
        lines = [l for l in out.splitlines() if l.startswith(name + " ")]
        self.assertEqual(len(lines), 1, out)
        return lines[0]

    def test_no_agents(self):
        self.assertIn("（没有 agent）", self.board())

    def test_lists_agents_with_state_and_current_tool(self):
        self.start("demo/idle", "claude")
        self.states_until("demo/idle", lambda s: s.get("state") == "idle")
        self.start("demo/busy", "claude", script=["tool:5"])
        self.states_until("demo/busy", lambda s: s.get("last_tool") == "Bash")
        out = self.board()
        for header in ("名字", "种类", "状态", "正在做", "接入", "最近输入"):
            self.assertIn(header, out.splitlines()[1])
        idle = self.row(out, "demo/idle")
        self.assertIn("claude", idle)
        self.assertIn("idle", idle)
        self.assertIn(self.status("demo/idle")["instance"][:6], idle)
        busy = self.row(out, "demo/busy")
        self.assertIn("working", busy)
        self.assertIn("Bash", busy)

    def test_prefix_filters(self):
        self.start("demo/a", "claude")
        self.start("other/b", "claude")
        self.states_until("other/b", lambda s: s.get("state") == "idle")
        out = self.board("--prefix", "demo/")
        self.row(out, "demo/a")
        self.assertNotIn("other/b", out)

    def test_read_only(self):
        self.start("demo/a", "claude")
        self.states_until("demo/a", lambda s: s.get("state") == "idle")
        before = self.status("demo/a")
        self.board()
        after = self.status("demo/a")
        self.assertEqual((after["instance"], after["last_input_at"], after["last_human_input"]),
                         (before["instance"], before["last_input_at"], before["last_human_input"]))


class BoardTerm(Window):
    """一个终端窗格：伪终端里跑 tools/board（面板或显示器）。"""

    def __init__(self, env, *args, rows=30, cols=120):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(sys.executable, [sys.executable, BOARD, *args], env)
        self.resize(rows, cols)
        self.output = b""
        self.exit_code = None


class PanelTest(AgentTestCase):
    """面板（tools/board）和显示器（tools/board --viewer）各占一个窗格，用假 agent 跑。"""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(self.home, "t")  # board 传话用的临时目录跟着 TMPDIR 走，测试之间互不干扰
        os.makedirs(self.tmp)
        self.env = dict(os.environ, CORRAL_HOME=self.home, SHELL=support.fast_shell(), TMPDIR=self.tmp,
                        TERM="xterm-256color")
        self.env.pop("CODEX_SANDBOX", None)
        self.terms = []

    def open(self, *args):
        t = BoardTerm(self.env, *args)
        self.terms.append(t)
        self.addCleanup(t.close)
        return t

    def until(self, pred, timeout=15.0):
        """一边读所有窗格的输出（免得它们写满卡住），一边等 pred 成立。"""
        end = time.time() + timeout
        while time.time() < end:
            for t in self.terms:
                t.pump(0.05)
            if pred():
                return True
        return False

    def seen(self, term, text):
        return self.until(lambda: text.encode() in term.output)

    def attached(self, name, n):
        return self.until(lambda: self.status(name).get("attached") == n)

    def idle_agents(self, *names, script=()):
        for name in names:
            self.start(name, "claude", script=script)
        for name in names:
            self.states_until(name, lambda s: s.get("state") == "idle")

    def test_enter_shows_selected_agent_in_viewer_and_switches(self):
        self.idle_agents("demo/a", "demo/b")
        viewer = self.open("--viewer")
        self.assertTrue(self.seen(viewer, "没有接入"))
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/b"))
        panel.type(b"\r")
        self.assertTrue(self.attached("demo/a", 1))
        viewer.type(b"reply:via-viewer\r")  # 在显示器里打的字送到了 demo/a
        self.assertTrue(self.until(lambda: self.cli("reply", "demo/a")[1].get("text") == "via-viewer"))
        mark = len(viewer.output)
        panel.type(b"j")
        panel.type(b"\r")
        self.assertTrue(self.attached("demo/b", 1))
        self.assertTrue(self.attached("demo/a", 0))
        self.assertIn(b"\x1b[?2004l", viewer.output[mark:])  # 换人时 attach 自己还原了 agent 打开的终端模式
        viewer.type(b"\x1d")  # Ctrl-] 断开，回到空闲画面
        self.assertTrue(self.attached("demo/b", 0))
        self.assertTrue(self.seen(viewer, "已断开 demo/b"))
        for name in ("demo/a", "demo/b"):
            self.assertEqual(self.status(name).get("state"), "idle")  # 换人、断开都不影响 agent

    def test_mouse_click_on_row_shows_that_agent(self):
        self.idle_agents("demo/a", "demo/b")
        self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/b"))
        y = 4  # 第 1 行标题、第 2 行表头、第 3 行 demo/a、第 4 行 demo/b（从 1 数）
        if re.search(rb"\x1b\[\?[\d;]*1006[\d;]*h", panel.output):  # 面板开了 SGR 格式的鼠标上报
            panel.type(f"\x1b[<0;5;{y}M\x1b[<0;5;{y}m".encode())
        else:
            panel.type(b"\x1b[M" + bytes([32, 32 + 5, 32 + y]) + b"\x1b[M" + bytes([35, 32 + 5, 32 + y]))
        self.assertTrue(self.attached("demo/b", 1))
        self.assertEqual(self.status("demo/a").get("attached"), 0)

    def test_agent_exit_returns_viewer_to_idle(self):
        self.idle_agents("demo/a")
        viewer = self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/a"))
        panel.type(b"\r")
        self.assertTrue(self.attached("demo/a", 1))
        self.assertEqual(self.cli("stop", "demo/a")[0], 0)
        self.assertTrue(self.seen(viewer, "demo/a 已退出"))

    def test_x_asks_and_only_y_stops(self):
        self.idle_agents("demo/a")
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/a"))
        panel.type(b"x")
        self.assertTrue(self.seen(panel, "按 y 确认"))
        panel.type(b"n")
        self.assertTrue(self.seen(panel, "已取消"))
        self.assertEqual(self.cli("status", "demo/a")[0], 0)
        panel.type(b"x")
        panel.type(b"y")
        self.assertTrue(self.until(lambda: self.cli("status", "demo/a")[0] == 2, timeout=30))

    def test_shows_last_reply_of_selected_agent(self):
        self.idle_agents("demo/a", script=["reply:hello-board"])
        self.states_until("demo/a", lambda s: s.get("last_event") == "Stop")
        panel = self.open()
        self.assertTrue(self.seen(panel, "hello-board"))

    def test_enter_refuses_agent_attached_elsewhere(self):
        self.idle_agents("demo/a")
        other = Window(self.home, "demo/a")  # 另一个终端里已经接入了
        self.terms.append(other)
        self.addCleanup(other.close)
        self.assertTrue(self.attached("demo/a", 1))
        viewer = self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/a"))
        panel.type(b"\r")
        self.assertTrue(self.seen(panel, "已在别处接入"))
        self.until(lambda: False, timeout=1.0)
        self.assertEqual(self.status("demo/a").get("attached"), 1)  # 右格没有接进去，不会只读乱显示
        self.assertNotIn(b"[corral]", viewer.output)

    def test_enter_without_viewer_tells_how_to_start_one(self):
        self.idle_agents("demo/a")
        panel = self.open()
        self.assertTrue(self.seen(panel, "demo/a"))
        panel.type(b"\r")
        self.assertTrue(self.seen(panel, "tools/board --viewer"))
        self.assertEqual(self.status("demo/a").get("attached"), 0)

    def test_viewer_ignores_instruction_left_before_it_started(self):
        self.idle_agents("demo/a")
        chan = os.path.join(self.tmp, f"corral-board-{os.getuid()}")
        os.makedirs(chan)
        with open(os.path.join(chan, "target"), "w") as f:
            json.dump({"name": "demo/a", "seq": 1}, f)
        viewer = self.open("--viewer")
        self.assertTrue(self.seen(viewer, "没有接入"))
        self.until(lambda: False, timeout=1.0)
        self.assertEqual(self.status("demo/a").get("attached"), 0)

    def test_only_one_viewer_and_q_quits(self):
        first = self.open("--viewer")
        self.assertTrue(self.seen(first, "没有接入"))
        second = self.open("--viewer")
        self.assertTrue(second.exited())
        self.assertNotEqual(second.exit_code, 0)
        self.assertIn("已经有一个显示器".encode(), second.output)
        first.type(b"q")
        self.assertTrue(first.exited())
        self.assertEqual(first.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
