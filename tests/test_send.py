"""M5：send / keys / wait（--quiet）/ reply / start --prompt / 输入来源。"""
import json
import time
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase
from tests.test_attach import Window
from corral import errors


class SendTest(AgentTestCase):
    def idle(self, name):
        self.states_until(name, lambda s: s.get("state") == "idle")

    def test_send_confirms_and_reply_is_exact(self):
        self.start("demo/c", "claude")
        self.idle("demo/c")
        text = "多行确认：\n  缩进的第二行\n```python\nprint('x')\n```"
        code, out = self.cli("send", "demo/c", text)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["confirmed"])
        code, st = self.cli("wait", "demo/c", "--timeout", "10")
        self.assertEqual((code, st["result"]), (0, "idle"))
        code, rep = self.cli("reply", "demo/c")
        self.assertEqual(code, 0)
        self.assertEqual(rep["text"], "echo: " + text)

    def test_single_line_send(self):
        self.start("demo/x", "codex", script=["reply:ready"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        code, out = self.cli("send", "demo/x", "?help first char")
        self.assertEqual(code, 0, out)
        self.cli("wait", "demo/x")
        self.assertEqual(self.cli("reply", "demo/x")[1]["text"], "echo: ?help first char")

    def test_not_idle_refused(self):
        self.start("demo/c", "claude", script=["slow:3"])
        self.states_until("demo/c", lambda s: s.get("state") == "working")
        code, out = self.cli("send", "demo/c", "hi")
        self.assertEqual(code, errors.EXIT_NOT_IDLE)
        self.assertEqual((out["error"], out["state"]), ("not_idle", "working"))

    def test_starting_codex_refused(self):
        self.start("demo/x", "codex")
        time.sleep(0.5)
        code, out = self.cli("send", "demo/x", "hi")
        self.assertEqual((code, out["state"]), (errors.EXIT_NOT_IDLE, "starting"))

    def test_not_delivered_does_not_press_extra_keys(self):
        self.start("demo/c", "claude", script=["menu"])
        self.states_until("demo/c", lambda s: s.get("last_event") == "Stop")
        code, out = self.cli("send", "demo/c", "swallowed", "--timeout", "2")
        self.assertEqual(code, errors.EXIT_NOT_DELIVERED)
        self.assertEqual(out["error"], "not_delivered")
        time.sleep(1.0)
        inputs = "".join(e["input"] for e in self.log_lines() if "input" in e)
        self.assertEqual(inputs.count("\r"), 1)

    def test_unknown_agent_is_written_but_not_confirmed(self):
        code, out = self.cli("start", "demo/s", "--cwd", self.home, "--", "sh", "-c", "cat > got.txt")
        self.assertEqual(code, 0)
        code, out = self.cli("send", "demo/s", "plain")
        self.assertEqual((code, out["confirmed"]), (0, False))
        path = f"{self.home}/got.txt"
        self.assertTrue(support.wait_until(lambda: "plain" in open(path).read()))


class HumanActiveTest(AgentTestCase):
    def window(self, name):
        w = Window(self.home, name)
        self.addCleanup(w.close)
        self.assertTrue(support.wait_until(lambda: self.status(name).get("attached") == 1))
        return w

    def test_recent_human_key_blocks_send_until_forced(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        w = self.window("demo/c")
        w.type(b"\x1b[A")  # 人按了方向键（不进输入框）
        self.assertTrue(support.wait_until(lambda: self.status("demo/c")["last_human_input"] is not None))
        code, out = self.cli("send", "demo/c", "hello")
        self.assertEqual((code, out["error"]), (errors.EXIT_HUMAN_ACTIVE, "human_active"))
        self.assertIsNotNone(out["last_human_input"])
        code, out = self.cli("send", "demo/c", "hello", "--force")
        self.assertEqual((code, out["confirmed"]), (0, True))

    def test_terminal_responses_do_not_block_send(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        w = self.window("demo/c")
        w.type(b"\x1b[I\x1b[O")
        time.sleep(0.3)
        code, out = self.cli("send", "demo/c", "hello")
        self.assertEqual((code, out.get("confirmed")), (0, True))

    def test_watching_without_typing_does_not_block(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        self.window("demo/c")
        code, out = self.cli("send", "demo/c", "hello")
        self.assertEqual((code, out.get("confirmed")), (0, True))


class InputSourceTest(AgentTestCase):
    def test_send_human_agent(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        self.cli("send", "demo/c", "from caller")
        st = self.cli("wait", "demo/c")[1]
        self.assertEqual(st["last_input_source"], "send")

        w = Window(self.home, "demo/c")
        self.addCleanup(w.close)
        self.assertTrue(support.wait_until(lambda: self.status("demo/c").get("attached") == 1))
        w.type(b"typed by human\r")
        self.states_until("demo/c", lambda s: s.get("last_input_source") == "human" and s["state"] == "idle")

    def test_start_prompt_counts_as_send(self):
        code, out = support.run_cli(["start", "demo/p", "--cwd", self.home, "--prompt", "reply:first",
                                     "--", support.fake_agent("codex")], home=self.home)
        self.assertEqual(code, 0, out)
        st = self.cli("wait", "demo/p")[1]
        self.assertEqual((st["result"], st["last_input_source"]), ("idle", "send"))

    def test_agent_started_turn(self):
        self.start("demo/c", "claude", script=["selfturn"])
        self.states_until("demo/c", lambda s: s.get("last_input_source") == "agent")


class WaitTest(AgentTestCase):
    def test_wait_returns_after_turn_end_with_reply(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        self.cli("send", "demo/c", "slow:1")
        t0 = time.time()
        code, st = self.cli("wait", "demo/c")
        self.assertEqual((code, st["result"]), (0, "idle"))
        self.assertGreater(time.time() - t0, 0.8)
        self.assertEqual(self.cli("reply", "demo/c")[1]["text"], "slow done")

    def test_wait_blocked(self):
        self.start("demo/c", "claude", script=["ask"])
        code, st = self.cli("wait", "demo/c", "--quiet", "0.5")
        self.assertEqual((code, st["result"]), (0, "blocked"))

    def test_quiet_only_when_working_without_output(self):
        self.start("demo/c", "claude", script=["hang"])
        self.states_until("demo/c", lambda s: s.get("state") == "working")
        t0 = time.time()
        code, st = self.cli("wait", "demo/c", "--quiet", "1")
        self.assertEqual((code, st["result"], st["state"]), (0, "stopped-quiet", "working"))
        self.assertLess(time.time() - t0, 5)

    def test_quiet_not_triggered_while_output_flows(self):
        self.start("demo/c", "claude", script=["slow:2.5"])
        self.states_until("demo/c", lambda s: s.get("state") == "working")
        code, st = self.cli("wait", "demo/c", "--quiet", "1")
        self.assertEqual((code, st["result"]), (0, "idle"))

    def test_timeout(self):
        self.start("demo/c", "claude", script=["hang"])
        self.states_until("demo/c", lambda s: s.get("state") == "working")
        code, st = self.cli("wait", "demo/c", "--timeout", "1")
        self.assertEqual((code, st["error"]), (errors.EXIT_TIMEOUT, "timeout"))
        self.assertIs(st["ok"], False)
        self.assertEqual(st["state"], "working")

    def test_codex_first_prompt_not_transient_idle(self):
        for swap in ("0", "1"):
            with self.subTest(swap=swap):
                name = f"demo/x{swap}"
                code, out = support.run_cli(
                    ["start", name, "--cwd", self.home, "--prompt", "slow:1", "--env", f"FAKE_SWAP_FIRST={swap}",
                     "--", support.fake_agent("codex")], home=self.home)
                self.assertEqual(code, 0, out)
                code, st = self.cli("wait", name, "--timeout", "15")
                self.assertEqual((code, st["result"]), (0, "idle"))
                self.assertEqual(self.cli("reply", name)[1]["text"], "slow done")

    def test_wait_on_starting_times_out_with_hint(self):
        self.start("demo/x", "codex")
        code, st = self.cli("wait", "demo/x", "--timeout", "1")
        self.assertEqual((code, st["state"]), (errors.EXIT_TIMEOUT, "starting"))
        self.assertIn("start", st["message"])


class KeysReplyPromptTest(AgentTestCase):
    def test_keys_esc_interrupts_codex(self):
        self.start("demo/x", "codex", script=["slow:10"])
        self.states_until("demo/x", lambda s: s.get("state") == "working")
        code, out = self.cli("keys", "demo/x", "esc")
        self.assertEqual(code, 0, out)
        self.states_until("demo/x", lambda s: s.get("state") == "idle" and s.get("last_event") == "Interrupt")

    def test_keys_text_and_unknown(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        code, _ = self.cli("keys", "demo/c", "text:hi there", "enter")
        self.assertEqual(code, 0)
        self.cli("wait", "demo/c")
        self.assertEqual(self.cli("reply", "demo/c")[1]["text"], "echo: hi there")
        code, out = self.cli("keys", "demo/c", "no-such-key")
        self.assertEqual((code, out["error"]), (errors.EXIT_ERROR, "usage"))

    def test_reply_before_any_turn(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        code, out = self.cli("reply", "demo/c")
        self.assertEqual((code, out["error"]), (errors.EXIT_ERROR, "no_reply"))

    def test_start_prompt_submitted_by_agent(self):
        code, out = support.run_cli(["start", "demo/c", "--cwd", self.home, "--prompt", "reply:first",
                                     "--", support.fake_agent("claude")], home=self.home)
        self.assertEqual(code, 0, out)
        code, st = self.cli("wait", "demo/c")
        self.assertEqual(self.cli("reply", "demo/c")[1]["text"], "first")

    def test_prompt_not_swallowed_by_variadic_option(self):
        # M8 实测：真 Claude Code 的 --allowedTools 接多个值，追加在最后的首句会被它吞掉
        code, out = support.run_cli(["start", "demo/v", "--cwd", self.home, "--prompt", "reply:kept",
                                     "--", support.fake_agent("claude"), "--allowedTools", "Bash(x:*)"],
                                    home=self.home)
        self.assertEqual(code, 0, out)
        code, st = self.cli("wait", "demo/v", "--timeout", "15")
        self.assertEqual((code, st.get("result")), (0, "idle"), st)
        self.assertEqual(self.cli("reply", "demo/v")[1].get("text"), "kept")

    def test_unsubmitted_prompt_is_starting_not_idle(self):
        code, out = support.run_cli(["start", "demo/u", "--cwd", self.home, "--prompt", "reply:never",
                                     "--env", "FAKE_IGNORE_PROMPT=1", "--", support.fake_agent("claude")],
                                    home=self.home)
        self.assertEqual(code, 0, out)
        time.sleep(1.0)
        self.assertEqual(self.status("demo/u")["state"], "starting")
        code, st = self.cli("wait", "demo/u", "--timeout", "2")
        self.assertEqual((code, st["state"]), (errors.EXIT_TIMEOUT, "starting"))

    def test_prompt_with_unknown_agent_rejected(self):
        code, out = self.cli("start", "demo/s", "--prompt", "hi", "--", "sleep", "5")
        self.assertEqual((code, out["error"]), (errors.EXIT_ERROR, "usage"))


if __name__ == "__main__":
    unittest.main()
