"""配套工具 tools/board：只读公开输出的终端看板。"""
import os
import subprocess
import sys
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase

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


if __name__ == "__main__":
    unittest.main()
