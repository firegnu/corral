"""pi 适配：用假 pi 跑通 启动 → 状态 → 送话确认 → 等待 → 取回复 → 打断 → 停止，以及委派和 send --after。"""
import json
import os
import stat
import time
import unittest
from unittest import mock

from tests import support
from tests.test_agent_state import AgentTestCase
from corral import errors, spawn


class PiStateTest(AgentTestCase):
    def idle(self, name):
        self.states_until(name, lambda s: s.get("state") == "idle")

    def test_starts_idle_with_private_extension_copy_and_no_hook_py(self):
        out = self.start("demo/p", "pi")
        self.assertEqual(out["kind"], "pi")
        self.idle("demo/p")
        d = os.path.join(self.home, "demo", "p")
        self.assertTrue(os.path.isfile(os.path.join(d, "hook_pi.ts")))
        self.assertFalse(os.path.exists(os.path.join(d, "hook.py")))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "hook_pi.ts")).st_mode), 0o600)
        argv = next(e["argv"] for e in self.log_lines() if "argv" in e)
        self.assertEqual(argv[1:3], ["--extension", os.path.join(d, "hook_pi.ts")])

    def test_does_not_need_system_python(self):
        with mock.patch.object(spawn, "HOOK_PYTHON", "/nonexistent/python3"):
            with mock.patch.dict(os.environ, {"CORRAL_HOME": self.home}):
                out = spawn.start("demo/p", self.home, [support.fake_agent("pi")])
        self.assertEqual(out["kind"], "pi")
        self.idle("demo/p")

    def test_tool_turn_and_reply(self):
        self.start("demo/p", "pi", script=["tool:1", "reply:hello"])
        seen, st = self.states_until("demo/p", lambda s: s.get("state") == "idle" and s.get("last_event") == "Stop"
                                     and s.get("turn_started") and s["last_tool"] is None)
        self.assertIn(("working", "Bash"), seen)
        self.assertEqual(self.cli("reply", "demo/p")[1]["text"], "hello")

    def test_blocked_on_extension_prompt(self):
        self.start("demo/p", "pi", script=["ask"])
        self.states_until("demo/p", lambda s: s.get("state") == "blocked")
        self.assertEqual(self.cli("keys", "demo/p", "text:1")[0], 0)
        self.states_until("demo/p", lambda s: s.get("state") == "idle" and s.get("last_event") == "Stop")

    def test_send_confirms_wait_and_reply_exact(self):
        self.start("demo/p", "pi")
        self.idle("demo/p")
        text = "多行：\n  第二行\n```sh\necho hi\n```"
        code, out = self.cli("send", "demo/p", text)
        self.assertEqual((code, out.get("confirmed")), (0, True), out)
        self.assertEqual(self.cli("wait", "demo/p", "--timeout", "10")[1]["result"], "idle")
        self.assertEqual(self.cli("reply", "demo/p")[1]["text"], "echo: " + text)
        self.assertEqual(self.status("demo/p")["last_input_source"], "send")

    def test_start_prompt(self):
        code, out = support.run_cli(["start", "demo/q", "--cwd", self.home, "--prompt", "reply:first",
                                     "--", support.fake_agent("pi")], home=self.home)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cli("wait", "demo/q", "--timeout", "10")[1]["result"], "idle")
        self.assertEqual(self.cli("reply", "demo/q")[1]["text"], "first")

    def test_esc_interrupts_to_idle(self):
        self.start("demo/p", "pi", script=["slow:30"])
        self.states_until("demo/p", lambda s: s.get("state") == "working")
        self.assertEqual(self.cli("keys", "demo/p", "esc")[0], 0)
        self.idle("demo/p")

    def test_stop_by_hangup(self):
        self.start("demo/p", "pi")
        self.idle("demo/p")
        t0 = time.time()
        code, out = self.cli("stop", "demo/p")
        self.assertEqual(code, 0, out)
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(out["stopped_by"], "SIGHUP")
        self.assertEqual(self.cli("status", "demo/p")[0], errors.EXIT_NOT_FOUND)


class PiDelegationTest(AgentTestCase):
    def idle(self, name):
        self.states_until(name, lambda s: s.get("state") == "idle")

    def test_pi_as_delegated_agent_is_waited_on_by_after(self):
        self.start("demo/main", "claude")
        self.idle("demo/main")
        self.start("demo/p", "pi", script=["slow:2"])
        self.states_until("demo/p", lambda s: s.get("state") == "working")
        self.assertEqual(self.cli("send", "demo/main", "p-done", "--after", "demo/p", "--timeout", "30")[0], 0)
        self.states_until("demo/main", lambda s: s.get("last_input_source") == "send" and s.get("state") == "idle",
                          timeout=25)
        self.assertEqual(self.cli("reply", "demo/main")[1]["text"], "echo: p-done")

    def test_pi_as_delegator_is_reminded_by_after(self):
        self.start("demo/p", "pi")
        self.idle("demo/p")
        self.start("demo/x", "codex", script=["slow:2"])
        self.states_until("demo/x", lambda s: s.get("state") == "working")
        self.assertEqual(self.cli("send", "demo/p", "x-done", "--after", "demo/x", "--timeout", "30")[0], 0)
        self.states_until("demo/p", lambda s: s.get("last_input_source") == "send" and s.get("state") == "idle",
                          timeout=25)
        self.assertEqual(self.cli("reply", "demo/p")[1]["text"], "echo: x-done")


if __name__ == "__main__":
    unittest.main()
