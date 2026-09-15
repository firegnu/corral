"""M6：stop 的退出顺序和等待；栏位协议版本兼容。"""
import json
import os
import signal
import subprocess
import sys
import time
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase
from tests.test_attach import Window
from corral import errors, protocol

FIXTURES = os.path.join(support.ROOT, "tests", "fixtures")


def exit_info(home, name):
    with open(os.path.join(home, name, "exit.json")) as f:
        return json.load(f)


class StopTest(AgentTestCase):
    def test_claude_stops_on_hangup(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        t0 = time.time()
        code, out = self.cli("stop", "demo/c")
        self.assertEqual(code, 0, out)
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(out["stopped_by"], "SIGHUP")
        self.assertEqual(self.cli("status", "demo/c")[0], errors.EXIT_NOT_FOUND)

    def test_codex_ignores_hangup_quits_with_own_keys(self):
        self.start("demo/x", "codex", script=["reply:ready"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        t0 = time.time()
        code, out = self.cli("stop", "demo/x")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["stopped_by"], out["exit_code"]), ("keys", 0))
        self.assertLess(time.time() - t0, 4)

    def test_codex_slow_graceful_shutdown_is_not_killed(self):
        # M8 实测：真 Codex 连按两次 Ctrl-C 后要收尾 7 秒多才退出，不能 5 秒就发 SIGTERM
        self.start("demo/x", "codex", script=["reply:ready"], extra_env=["FAKE_SHUTDOWN_DELAY=8"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        code, out = self.cli("stop", "demo/x")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["stopped_by"], out["exit_code"]), ("keys", 0))

    def test_escalates_to_term_when_quit_keys_ignored(self):
        self.start("demo/x", "codex", extra_env=["FAKE_IGNORE_CTRL_C=1"])
        time.sleep(0.5)
        code, out = self.cli("stop", "demo/x")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["stopped_by"], out["exit_code"]), ("SIGTERM", -signal.SIGTERM))

    def test_escalates_to_kill(self):
        code, out = self.cli("start", "demo/s", "--cwd", self.home, "--", "sh", "-c",
                             'trap "" HUP TERM; while :; do sleep 0.1; done')
        self.assertEqual(code, 0)
        code, out = self.cli("stop", "demo/s", "--timeout", "20")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["stopped_by"], "SIGKILL")

    def test_same_name_start_right_after_stop(self):
        self.start("demo/x", "codex", script=["reply:ready"])
        self.states_until("demo/x", lambda s: s.get("last_event") == "Stop")
        self.assertEqual(self.cli("stop", "demo/x")[0], 0)
        self.start("demo/x", "codex")

    def test_background_process_left_alone(self):
        pidfile = os.path.join(self.home, "bg.pid")
        script = os.path.join(self.home, "bg.py")
        with open(script, "w") as f:  # agent 起一个脱离自己进程组的后台进程
            f.write(f"import os, time\nos.setsid()\nopen({pidfile!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
        code, _ = self.cli("start", "demo/s", "--cwd", self.home, "--", "sh", "-c",
                           f"{sys.executable} {script} & cat > /dev/null")
        self.assertEqual(code, 0)
        self.assertTrue(support.wait_until(lambda: os.path.exists(pidfile) and open(pidfile).read()))
        pid = int(open(pidfile).read())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL))
        self.assertEqual(self.cli("stop", "demo/s")[0], 0)
        os.kill(pid, 0)  # 还活着

    def test_stop_not_running(self):
        code, out = self.cli("stop", "demo/nobody")
        self.assertEqual((code, out["error"]), (errors.EXIT_NOT_FOUND, "not_found"))

    def test_stop_timeout(self):
        code, out = self.cli("start", "demo/s", "--cwd", self.home, "--", "sh", "-c",
                             'trap "" HUP TERM; while :; do sleep 0.1; done')
        code, out = self.cli("stop", "demo/s", "--timeout", "1")
        self.assertEqual((code, out["error"]), (errors.EXIT_TIMEOUT, "timeout"))
        support.wait_until(lambda: self.cli("status", "demo/s")[0] == errors.EXIT_NOT_FOUND, timeout=15)

    def test_attached_window_sees_exit(self):
        self.start("demo/c", "claude")
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        w = Window(self.home, "demo/c")
        self.addCleanup(w.close)
        self.assertTrue(support.wait_until(lambda: self.status("demo/c").get("attached") == 1))
        self.assertEqual(self.cli("stop", "demo/c")[0], 0)
        self.assertTrue(w.exited())


class OldProtocolPenTest(unittest.TestCase):
    """用 tests/fixtures 里冻结的上一个协议版本的栏位，检查当前命令能不能操作它。"""

    def setUp(self):
        self.home = support.short_tmpdir()
        self.addCleanup(support.kill_all, self.home)

    def cli(self, *args):
        return support.run_cli(list(args), home=self.home)

    def start_old(self, name, argv, report_proto=None):
        code = (
            "import json, sys\n"
            f"sys.path[:0] = [{support.SRC!r}, {FIXTURES!r}]\n"
            "from corral import spawn\n"
            "import corral_proto1.pen as oldpen, corral_proto1.protocol as oldproto\n"
            f"if {report_proto!r} is not None: oldproto.PROTOCOL_VERSION = {report_proto!r}\n"
            "spawn.pen = oldpen\n"
            f"print(json.dumps(spawn.start({name!r}, {self.home!r}, {argv!r})))\n"
        )
        env = dict(os.environ, CORRAL_HOME=self.home, SHELL=support.fast_shell())
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_fixture_is_previous_or_current_version(self):
        sys.path.insert(0, FIXTURES)
        try:
            import corral_proto1.protocol as oldproto
        finally:
            sys.path.remove(FIXTURES)
        self.assertEqual(oldproto.PROTOCOL_VERSION, 1)
        self.assertIn(1, protocol.SUPPORTED_PROTOCOLS)
        self.assertGreaterEqual(1, protocol.PROTOCOL_VERSION - 1)

    def test_current_commands_work_with_old_pen(self):
        self.start_old("demo/old", ["sh", "-c", "cat > got.txt"])
        code, st = self.cli("status", "demo/old")
        self.assertEqual((code, st["proto"]), (0, 1))
        code, out = self.cli("send", "demo/old", "hello old pen")
        self.assertEqual(code, 0, out)
        self.assertTrue(support.wait_until(lambda: "hello old pen" in open(os.path.join(self.home, "got.txt")).read()))
        self.assertIn("demo/old", [a["name"] for a in self.cli("ls")[1]["agents"]])
        w = Window(self.home, "demo/old")
        self.addCleanup(w.close)
        self.assertTrue(support.wait_until(lambda: self.cli("status", "demo/old")[1].get("attached") == 1))
        w.type(b"\x1d")
        self.assertTrue(w.exited())
        code, out = self.cli("stop", "demo/old")
        self.assertEqual(code, 0, out)

    def test_unknown_protocol_version_is_incompatible(self):
        self.start_old("demo/new", ["sleep", "30"], report_proto=99)
        for args in (["status", "demo/new"], ["send", "demo/new", "x"], ["stop", "demo/new"]):
            with self.subTest(args=args):
                code, out = self.cli(*args)
                self.assertEqual((code, out["error"]), (errors.EXIT_INCOMPATIBLE, "incompatible"))
        code, out = self.cli("ls")
        self.assertEqual(code, 0)
        [entry] = out["agents"]
        self.assertTrue(entry["incompatible"])


if __name__ == "__main__":
    unittest.main()
