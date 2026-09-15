"""M4：用假 agent 跑通 钩子 → 事件文件 → 状态。"""
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from tests import support
from corral import errors, spawn


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.home = support.short_tmpdir()
        self.log = os.path.join(self.home, "fake.log")
        self.addCleanup(support.kill_all, self.home)

    def cli(self, *args, **kw):
        return support.run_cli(list(args), home=self.home, **kw)

    def start(self, name, flavor, script=(), extra_env=(), args=(), bin_path=None):
        env_args = ["--env", f"FAKE_LOG={self.log}", "--env", f"FAKE_SCRIPT={json.dumps(list(script))}"]
        for e in extra_env:
            env_args += ["--env", e]
        cmd = [bin_path or support.BIN, "start", name, "--cwd", self.home, *env_args, "--",
               support.fake_agent(flavor), *args]
        proc = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, CORRAL_HOME=self.home, SHELL=support.fast_shell()))
        out = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 0, out)
        return out

    def status(self, name):
        return self.cli("status", name)[1]

    def states_until(self, name, pred, timeout=15.0):
        """反复查状态，记下看到过的 (state, last_tool)，直到 pred(status) 为真。"""
        seen = []
        end = time.time() + timeout
        while time.time() < end:
            st = self.status(name)
            key = (st.get("state"), st.get("last_tool"))
            if not seen or seen[-1] != key:
                seen.append(key)
            if pred(st):
                return seen, st
            time.sleep(0.05)
        self.fail(f"timeout; seen {seen}, last {st}")

    def log_lines(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(l) for l in f]


class ClaudeStateTest(AgentTestCase):
    def test_idle_working_tool_idle(self):
        self.start("demo/c", "claude", script=["tool:1", "reply:hello"])
        seen, st = self.states_until("demo/c", lambda s: s.get("state") == "idle" and s.get("last_event") == "Stop"
                                     and s.get("turn_started") and s["last_tool"] is None)
        self.assertIn(("working", "Bash"), seen)
        self.assertEqual(st["kind"], "claude")

    def test_ask_is_blocked(self):
        self.start("demo/c", "claude", script=["ask"])
        seen, st = self.states_until("demo/c", lambda s: s.get("state") == "blocked")
        self.assertEqual(st["last_tool"], "AskUserQuestion")

    def test_hooks_use_private_copy_and_print_nothing(self):
        self.start("demo/c", "claude", script=["tool:0.2"])
        self.states_until("demo/c", lambda s: s.get("last_event") == "Stop")
        entries = self.log_lines()
        argv = next(e["argv"] for e in entries if "argv" in e)
        settings = json.loads(argv[argv.index("--settings") + 1])
        hook_copy = os.path.join(self.home, "demo", "c", "hook.py")
        for entries_ in settings["hooks"].values():
            cmd = entries_[0]["hooks"][0]["command"]
            self.assertTrue(cmd.startswith(f"/usr/bin/python3 -I -S {hook_copy} "), cmd)
            self.assertNotIn(support.ROOT, cmd)
        self.assertFalse([e for e in entries if "hook_stdout" in e])

    def test_internal_files_private(self):
        old = os.umask(0)
        try:
            self.start("demo/c", "claude", script=["reply:x"])
            self.states_until("demo/c", lambda s: s.get("last_event") == "Stop")
        finally:
            os.umask(old)
        for f in ("hook.py", "events", "cursor"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.home, "demo", "c", f)).st_mode), 0o600, f)


class CodexStateTest(AgentTestCase):
    def test_starting_until_first_submit(self):
        self.start("demo/x", "codex")
        time.sleep(1.0)
        self.assertEqual(self.status("demo/x")["state"], "starting")

    def test_subagent_events_do_not_affect_main(self):
        self.start("demo/x", "codex", script=["subagent"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        time.sleep(1.0)  # 子会话在主会话结束后还会继续产生事件
        st = self.status("demo/x")
        self.assertEqual((st["state"], st["last_tool"]), ("idle", None))

    def test_input_before_session_start_on_disk(self):
        self.start("demo/x", "codex", script=["slow:3"], extra_env=["FAKE_SWAP_FIRST=1"])
        self.states_until("demo/x", lambda s: s.get("state") == "working")
        time.sleep(0.5)
        self.assertEqual(self.status("demo/x")["state"], "working")

    def test_hooks_passed_with_trust_bypass(self):
        self.start("demo/x", "codex", script=["reply:ok"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        argv = next(e["argv"] for e in self.log_lines() if "argv" in e)
        self.assertIn("--dangerously-bypass-hook-trust", argv)


class UnknownAgentTest(AgentTestCase):
    def test_no_hooks_no_hook_copy(self):
        code, out = self.cli("start", "demo/s", "--cwd", self.home, "--", "sleep", "30")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status("demo/s")["state"], "unknown")
        self.assertFalse(os.path.exists(os.path.join(self.home, "demo", "s", "hook.py")))


class HookIndependenceTest(AgentTestCase):
    def test_hooks_keep_working_after_repo_copy_is_deleted(self):
        # 用仓库的临时副本启动，然后删掉副本：正在跑的 agent 的钩子不能受影响
        copy = os.path.join(support.short_tmpdir(), "corral-copy")
        shutil.copytree(support.ROOT, copy, ignore=shutil.ignore_patterns(".git", "spike", "tests", "__pycache__"))
        self.start("demo/c", "claude", script=["slow:2", "reply:after-delete"],
                   bin_path=os.path.join(copy, "bin", "corral"))
        shutil.rmtree(copy)
        # 删掉副本之后才发生的第二轮，事件照样记到、状态照样算出来
        self.assertTrue(support.wait_until(
            lambda: self.cli("reply", "demo/c")[1].get("text") == "after-delete", timeout=20))
        self.assertEqual(self.cli("wait", "demo/c", "--timeout", "10")[1]["result"], "idle")


class HookPythonCheckTest(unittest.TestCase):
    def test_missing_system_python_is_reported(self):
        with mock.patch.object(spawn, "HOOK_PYTHON", "/nonexistent/python3"):
            with self.assertRaises(errors.CorralError) as cm:
                spawn.check_hook_python()
        self.assertEqual(cm.exception.error, "hook_python_unavailable")

    def test_system_python_ok(self):
        if os.path.exists("/usr/bin/python3"):
            spawn.check_hook_python()


if __name__ == "__main__":
    unittest.main()
