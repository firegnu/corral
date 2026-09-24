"""配套工具 tools/board：只读公开输出的终端看板，面板加显示器。"""
import importlib.util
import json
import os
import pty
import re
import subprocess
import sys
import time
import unittest

from importlib.machinery import SourceFileLoader

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
        self.assertIn("(no agents)", self.board())

    def test_lists_agents_with_state_and_current_tool(self):
        self.start("demo/idle", "claude")
        self.states_until("demo/idle", lambda s: s.get("state") == "idle")
        self.start("demo/busy", "claude", script=["tool:5"])
        self.states_until("demo/busy", lambda s: s.get("last_tool") == "Bash")
        out = self.board()
        for header in ("NAME", "KIND", "STATE", "DOING", "ATT", "SOURCE"):
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
        self.assertTrue(self.seen(viewer, "Not attached"))
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 2"))
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
        self.assertTrue(self.seen(viewer, "detached from demo/b"))
        for name in ("demo/a", "demo/b"):
            self.assertEqual(self.status(name).get("state"), "idle")  # 换人、断开都不影响 agent

    def test_mouse_click_on_row_shows_that_agent(self):
        self.idle_agents("demo/a", "demo/b")
        self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 2"))
        y = 6  # 第 1 行标题、第 2 行框的上边、第 3 行表头、第 4 行分组小标题、第 5 行 demo/a、第 6 行 demo/b（从 1 数）
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
        self.assertTrue(self.seen(panel, " · 1"))
        panel.type(b"\r")
        self.assertTrue(self.attached("demo/a", 1))
        self.assertEqual(self.cli("stop", "demo/a")[0], 0)
        self.assertTrue(self.seen(viewer, "demo/a exited"))

    def test_x_asks_and_only_y_stops(self):
        self.idle_agents("demo/a")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 1"))
        panel.type(b"x")
        self.assertTrue(self.seen(panel, "y to confirm"))
        panel.type(b"n")
        self.assertTrue(self.seen(panel, "cancelled"))
        self.assertEqual(self.cli("status", "demo/a")[0], 0)
        panel.type(b"x")
        panel.type(b"y")
        self.assertTrue(self.until(lambda: self.cli("status", "demo/a")[0] == 2, timeout=30))

    def test_shows_last_reply_of_selected_agent(self):
        self.idle_agents("demo/a", script=["reply:hello-board"])
        self.states_until("demo/a", lambda s: s.get("last_event") == "Stop")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 1"))
        self.until(lambda: False, timeout=1.0)
        self.assertNotIn(b"hello-board", panel.output)  # 回复区默认不显示，也不去取
        panel.type(b"r")
        self.assertTrue(self.seen(panel, "hello-board"))

    def test_spinner_keeps_turning_while_working(self):
        self.start("demo/a", "claude", script=["tool:60"])
        self.states_until("demo/a", lambda s: s.get("last_tool") == "Bash")
        panel = self.open()
        self.assertTrue(self.seen(panel, "Bash"))
        mark = len(panel.output)
        self.until(lambda: False, timeout=1.5)
        frames = {ch for ch in panel.output[mark:].decode("utf-8", "replace") if ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"}
        self.assertGreaterEqual(len(frames), 3, frames)

    def test_wheel_scrolls_reply_and_does_not_pick_rows(self):
        self.idle_agents("demo/a", script=["reply:" + "a" * 3000 + "END"])
        self.states_until("demo/a", lambda s: s.get("last_event") == "Stop")
        self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 1"))
        panel.type(b"r")
        self.assertTrue(self.seen(panel, "aaaa"))
        self.until(lambda: False, timeout=0.5)
        self.assertNotIn(b"END", panel.output)  # 回复比回复区长，末尾还没露出来
        wheel_down = "\x1b[<65;10;{}M"  # SGR 格式的滚轮向下，x=10，y 从 1 数
        panel.type(wheel_down.format(5).encode() * 3)  # 第 5 行是 demo/a 这一行：只滚动，不接入
        panel.type(wheel_down.format(15).encode() * 20)  # 回复区里：一格滚一行，滚过头也只停在末尾
        self.assertTrue(self.seen(panel, "END"))
        self.assertEqual(self.status("demo/a").get("attached"), 0)

    def test_enter_refuses_agent_attached_elsewhere(self):
        self.idle_agents("demo/a")
        other = Window(self.home, "demo/a")  # 另一个终端里已经接入了
        self.terms.append(other)
        self.addCleanup(other.close)
        self.assertTrue(self.attached("demo/a", 1))
        viewer = self.open("--viewer")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 1"))
        panel.type(b"\r")
        self.assertTrue(self.seen(panel, "attached elsewhere"))
        self.until(lambda: False, timeout=1.0)
        self.assertEqual(self.status("demo/a").get("attached"), 1)  # 右格没有接进去，不会只读乱显示
        self.assertNotIn(b"[corral]", viewer.output)

    def test_enter_without_viewer_tells_how_to_start_one(self):
        self.idle_agents("demo/a")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 1"))
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
        self.assertTrue(self.seen(viewer, "Not attached"))
        self.until(lambda: False, timeout=1.0)
        self.assertEqual(self.status("demo/a").get("attached"), 0)

    def test_groups_and_marks_turn_that_just_finished(self):
        self.idle_agents("demo/a", "demo/b")
        panel = self.open()
        self.assertTrue(self.seen(panel, " · 2"))
        self.assertEqual(self.cli("send", "demo/b", "reply:done")[0], 0)  # 光标在 demo/a 上，demo/b 做完一轮
        self.assertTrue(self.seen(panel, "●"))

    def test_only_one_viewer_and_q_quits(self):
        first = self.open("--viewer")
        self.assertTrue(self.seen(first, "Not attached"))
        second = self.open("--viewer")
        self.assertTrue(second.exited())
        self.assertNotEqual(second.exit_code, 0)
        self.assertIn(b"a viewer is already running", second.output)
        first.type(b"q")
        self.assertTrue(first.exited())
        self.assertEqual(first.exit_code, 0)


def load_board():
    loader = SourceFileLoader("board", BOARD)
    spec = importlib.util.spec_from_loader("board", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def rec(name, state="idle", instance="i1", last_output=None, started=None, attached=0, cwd="/tmp/x", turn=None):
    """面板的一条记录，和 collect() 产出的一样。"""
    st = {"state": state, "instance": instance, "kind": "claude", "last_output": last_output, "started": started,
          "attached": attached, "turn_started": turn, "last_tool": None}
    return {"name": name, "state": state, "attached": attached, "st": st, "instance": instance, "cwd": cwd,
            "starting": False, "cells": (name, "claude", instance, state, "", "", str(attached), "", "")}


class PanelLogicTest(unittest.TestCase):
    """面板里不需要终端的部分：直接加载 tools/board 调函数。"""

    def setUp(self):
        self.board = load_board()
        self.board.viewer_info = lambda: None  # 不去碰本机真实的传话目录

    def panel(self, **kw):
        return self.board.Panel(None, 3.0, **kw)

    def screen_class(self):
        board = self.board

        class Screen:  # 只记字符的假屏幕
            def __init__(s, h, w):
                s.h, s.w, s.g = h, w, [[" "] * w for _ in range(h)]

            def getmaxyx(s):
                return s.h, s.w

            def erase(s):
                s.g = [[" "] * s.w for _ in range(s.h)]

            def addstr(s, y, x, text, attr=0):
                for ch in text:
                    for i in range(board.width(ch)):  # 中文占两格，第二格留空
                        if x < s.w:
                            s.g[y][x] = "" if i else ch
                        x += 1

            insstr = addstr

            def refresh(s):
                pass

        return Screen

    def drawable_panel(self):
        """能直接 draw 进假屏幕的面板：按无色终端画。"""
        curses = self.board.curses
        self.addCleanup(setattr, curses, "has_colors", curses.has_colors)  # 共用的 curses 模块，测完还原
        curses.has_colors = lambda: False
        p = self.panel()
        p.gray_fg, p.bar_bg, p.bar_extra, p.sel_fg, p.sel_bg, p.bar = -1, -1, 0, -1, -1, 0
        return p

    def test_short_dir(self):
        short = self.board.short_dir
        self.assertEqual(short("/Users/u/Developer/p/owlet", "/Users/u"), "…/p/owlet")
        self.assertEqual(short("/Users/u/proj", "/Users/u"), "~/proj")
        self.assertEqual(short("/Users/u", "/Users/u"), "~")
        self.assertEqual(short("/tmp/crt-x", "/Users/u"), "/tmp/crt-x")
        self.assertEqual(short(None, "/Users/u"), "")

    def test_markdown(self):
        lines = self.board.md_lines("# 标题\n- 一项 **重点** 和 `code`\n```\nx = 1\n```\n普通", 40)
        self.assertEqual(lines, [
            [("标题", "head")],
            [("• ", "bullet"), ("一项 ", None), ("重点", "bold"), (" 和 ", None), ("code", "code")],
            [("│ ", "rule"), ("x = 1", "code")],
            [("普通", None)],
        ])

    def test_markdown_wraps_with_hanging_indent(self):
        lines = self.board.md_lines("- " + "字" * 7, 12)
        self.assertEqual(lines, [[("• ", "bullet"), ("字" * 5, None)], [("  ", None), ("字" * 2, None)]])

    def test_narrow_width_hides_columns_in_order(self):
        keys = [k for k, _, _ in self.board.PANEL_COLUMNS]
        widths = dict.fromkeys(keys, 5)
        self.assertEqual(self.board.fit_columns(widths, 100), keys)
        self.assertEqual(self.board.fit_columns(widths, 60), [k for k in keys if k not in ("title", "instance")])
        self.assertEqual(self.board.fit_columns(widths, 10), ["name", "state", "doing", "attached"])

    def test_finished_turn_is_marked_until_selected(self):
        p = self.panel()
        p.absorb([rec("demo/a", "working"), rec("demo/b", "idle")])
        self.assertEqual(p.new, set())  # 刚打开时看到的不算变化
        p.absorb([rec("demo/a", "idle"), rec("demo/b", "idle")])
        self.assertEqual(p.new, {"demo/a"})
        self.assertEqual(p.marker(p.records[0], time.time()), "●")
        p.select("demo/a")
        self.assertEqual(p.new, set())

    def test_short_turn_between_refreshes_is_marked(self):
        p = self.panel()
        p.absorb([rec("demo/a", "idle", turn=100.0)])
        p.absorb([rec("demo/a", "idle", turn=100.0)])
        self.assertEqual(p.new, set())
        p.absorb([rec("demo/a", "idle", turn=200.0)])  # 两次刷新之间跑完了一整轮，没看到 working
        self.assertEqual(p.new, {"demo/a"})

    def test_selected_agent_is_not_marked(self):
        p = self.panel()
        p.selected = "demo/a"
        p.absorb([rec("demo/a", "working")])
        p.absorb([rec("demo/a", "idle")])
        self.assertEqual(p.new, set())

    def test_exit_is_reported_and_restart_is_not_a_change(self):
        p = self.panel()
        p.absorb([rec("demo/a", "working"), rec("demo/b")])
        p.absorb([rec("demo/a", "working")])
        self.assertIn("demo/b exited", p.message)
        p.absorb([rec("demo/a", "idle", instance="i2")])  # 同名重开，是另一个实例
        self.assertEqual(p.new, set())

    def test_bell_on_turn_end_and_blocked(self):
        p = self.panel(bell=True)
        p.absorb([rec("demo/a", "working")])
        p.absorb([rec("demo/a", "blocked")])
        self.assertTrue(p.ring)
        p.ring = False
        p.absorb([rec("demo/a", "working")])
        self.assertFalse(p.ring)
        p.absorb([rec("demo/a", "idle")])
        self.assertTrue(p.ring)
        quiet = self.panel()
        quiet.absorb([rec("demo/a", "working")])
        quiet.absorb([rec("demo/a", "blocked")])
        self.assertFalse(quiet.ring)

    def test_suspect_stuck(self):
        now = time.time()
        p = self.panel(stuck_start=60, stuck_quiet=120)
        self.assertEqual(p.marker(rec("demo/a", "working", last_output=now - 200), now), "?")
        self.assertEqual(p.marker(rec("demo/a", "working", last_output=now - 10), now), " ")
        self.assertEqual(p.marker(rec("demo/a", "starting", started=now - 100), now), "?")
        self.assertEqual(p.marker(rec("demo/a", "starting", started=now - 10), now), " ")
        self.assertEqual(p.marker(rec("demo/a", "blocked", last_output=now - 200), now), "!")

    def test_groups_and_sort_by_state(self):
        p = self.panel()
        p.absorb([rec("demo/c"), rec("owlet/a", "working"), rec("owlet/b"), rec("owlet/z", "blocked")])
        now = time.time()

        def shape(rows):
            return [(r["group"], r["count"]) if "count" in r else r["rec"]["name"] for r in rows]

        self.assertEqual(shape(p.view(now)), [("demo/", 1), "demo/c", ("owlet/", 3), "owlet/a", "owlet/b", "owlet/z"])
        p.by_state = True
        self.assertEqual(shape(p.view(now))[2:], [("owlet/", 3), "owlet/z", "owlet/a", "owlet/b"])
        self.assertEqual(p.cells(p.records[1], "owlet/", now)["name"], "a")

    def test_scrollbar_and_short_hints(self):
        Screen = self.screen_class()
        p = self.drawable_panel()
        p.absorb([rec("demo/a")])
        p.selected = "demo/a"
        p.show_reply = True
        p.reply = ("demo/a", "\n".join(f"第 {i} 行" for i in range(60)), False)
        wide, mid, narrow = Screen(20, 100), Screen(20, 75), Screen(20, 60)
        p.draw(wide, None)
        p.draw(mid, None)
        p.draw(narrow, None)
        top = next(i for i, row in enumerate(wide.g) if row[0] == "├") + 1  # 回复区从分节线下一行到框的下边
        right = {row[-2] for row in wide.g[top:-2]}
        self.assertEqual(right, {"░", "█"})  # 回复放不下：边框里侧那一列是滚动条
        self.assertIn("enter/click show in viewer", "".join(wide.g[19]))
        self.assertIn("enter show", "".join(mid.g[19]))  # 不足 80 列换短提示
        self.assertIn("⏎ show", "".join(narrow.g[19]))  # 不足 64 列再缩一档，六个键都在
        self.assertIn("q quit", "".join(narrow.g[19]))

    def test_narrow_wraps_identity_under_the_row_without_losing_anything(self):
        Screen = self.screen_class()
        p = self.drawable_panel()
        title = "Login form validation for checkout"  # 比单行表格里的 28 格长
        a = rec("demo/alice", instance="a9a2be000000", attached=1, cwd="/tmp/wt/owlet-m3-api")
        a["st"].update(title=title, last_input_source="send")
        b = rec("demo/bob", instance="b5fe53000000", cwd="/tmp/wt/owlet-m3-web")
        p.absorb([a, b])
        p.selected = "demo/alice"
        for w in (70, 200):
            screen = Screen(30, w)
            p.draw(screen, None)
            lines = ["".join(row) for row in screen.g]
            at = next(i for i, line in enumerate(lines) if "alice" in line)
            end = next(i for i, line in enumerate(lines) if "bob" in line)
            block = " ".join(lines[at:end])
            for value in ("alice", "idle", "claude", "a9a2be", "send", "owlet-m3-api"):
                self.assertIn(value, block, (w, value))
            if w == 70:  # 放不下一行：下面几行按项折行，标题完整；表头只有一行；接入数为 0 不写
                self.assertGreater(end - at, 1)
                self.assertIn(title, block)
                self.assertIn("attached 1", block)
                self.assertFalse(any("KIND" in line for line in lines))
                self.assertNotIn("attached", " ".join(lines[end:end + 3]))
            else:  # 宽屏仍是单行表格
                self.assertEqual(end - at, 1)

    def test_r_toggles_reply_section(self):
        Screen = self.screen_class()
        p = self.drawable_panel()
        p.absorb([rec("demo/a")])
        p.selected = "demo/a"
        p.reply = ("demo/a", "hello-board", False)

        def drawn():
            screen = Screen(20, 52)
            p.draw(screen, None)
            return ["".join(row) for row in screen.g]

        lines = drawn()  # 默认不显示：列表占满框，底栏提示 r reply
        self.assertFalse(any("last reply" in line or "hello-board" in line for line in lines))
        self.assertIn("r reply", lines[-1])
        self.assertEqual(lines[-2][0], "╰")
        p.handle(ord("r"), 5)
        lines = drawn()
        self.assertTrue(any("last reply" in line for line in lines))
        self.assertTrue(any("hello-board" in line for line in lines))
        self.assertIn("r hide", lines[-1])
        self.assertIn("q quit", lines[-1])
        p.handle(ord("r"), 5)
        self.assertFalse(any("hello-board" in line for line in drawn()))

    def test_very_narrow_keeps_doing_quiet_and_every_key(self):
        Screen = self.screen_class()
        p = self.drawable_panel()
        now = time.time()
        a = rec("demo/dev-board-demo-1", "working", last_output=now - 7, turn=now - 192)
        a["st"]["last_tool"] = "exec_command"
        p.absorb([a, rec("demo/main")])
        p.selected = "demo/main"
        screen = Screen(30, 52)
        p.draw(screen, None)
        lines = ["".join(row) for row in screen.g]
        at = next(i for i, line in enumerate(lines) if "dev-board-demo-1" in line)
        end = next(i for i, line in enumerate(lines) if i > at and " main " in line)
        block = " ".join(lines[at:end])
        self.assertIn("exec_command", block)
        self.assertRegex(block, r"\b19\ds\b")  # 这一轮的用时没被挤出去
        self.assertRegex(lines[at], r"\b7s\b")  # 「无输出」留在第一行
        self.assertIn("╰", lines[end - 1])  # 归属引线在这个 agent 的最后一行收口
        self.assertIn("q quit", lines[-1])  # 底栏六个键都在

    def test_spinner_turns_on_working_rows(self):
        p = self.panel()
        r = rec("demo/a", "working")
        p.frame = 0
        first = p.cells(r, "demo/", time.time())["doing"]
        p.frame = 1
        second = p.cells(r, "demo/", time.time())["doing"]
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1:], second[1:])
        self.assertEqual(p.cells(rec("demo/a", "idle"), "demo/", time.time())["doing"], "")


if __name__ == "__main__":
    unittest.main()
