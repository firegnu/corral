"""M2：agent 的环境从用户的登录 shell 重建。"""
import os
import stat
import time
import unittest
from unittest import mock

from tests import support
from corral import env, errors

NOISY_RC = r"""#!/bin/sh
# 假登录 shell：像 shell 配置文件一样往外打印、设变量，再执行 -c 的脚本
echo "welcome from rc"
printf 'more noise without newline'
export FOO=bar
export MULTI="line1
line2"
export PATH="/custom/bin:$PATH"
export CODEX_THREAD_ID=leaked
export CODEX_HOME=/users/codex/home
export TMUX=/tmp/tmux-1/default,1,0
export CORRAL_NAME=someone-else
eval "$4"
echo "bye from rc"
"""

SLOW_RC = """#!/bin/sh
sleep 30
eval "$4"
"""

BROKEN_RC = """#!/bin/sh
echo "no markers here"
exit 3
"""


def write_shell(body):
    d = support.short_tmpdir()
    path = os.path.join(d, "fakeshell")
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, stat.S_IRWXU)
    return path


IDENTITY = dict(name="demo/alice", instance="0123456789ab", events="/tmp/h/demo/alice/events", home="/tmp/h")


class LoginShellEnvTest(unittest.TestCase):
    def test_takes_login_shell_env_and_ignores_rc_output(self):
        shell = write_shell(NOISY_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell, "CALLER_ONLY": "1"}):
            result, warnings = env.build(extra={}, **IDENTITY)
        self.assertEqual(warnings, [])
        self.assertEqual(result["FOO"], "bar")
        self.assertEqual(result["MULTI"], "line1\nline2")
        self.assertTrue(result["PATH"].startswith("/custom/bin:"))
        self.assertNotIn("CALLER_ONLY", result)      # 调用方的变量进不来
        self.assertEqual(result["SHELL"], shell)

    def test_strips_session_variables_but_keeps_user_config(self):
        shell = write_shell(NOISY_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell}):
            result, _ = env.build(extra={}, **IDENTITY)
        self.assertNotIn("CODEX_THREAD_ID", result)
        self.assertNotIn("TMUX", result)
        self.assertEqual(result["CODEX_HOME"], "/users/codex/home")   # 用户配置，不是会话变量
        self.assertEqual(result["CORRAL_NAME"], "demo/alice")          # 身份由 corral 重新设置

    def test_identity_terminal_and_extra(self):
        shell = write_shell(NOISY_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell}):
            result, _ = env.build(extra={"FOO": "override", "NEW": "x", "CORRAL_INSTANCE": "nope"}, **IDENTITY)
        self.assertEqual(result["FOO"], "override")
        self.assertEqual(result["NEW"], "x")
        self.assertEqual(result["CORRAL_INSTANCE"], "0123456789ab")    # --env 盖不掉身份
        self.assertEqual(result["CORRAL_EVENTS"], "/tmp/h/demo/alice/events")
        self.assertEqual(result["CORRAL_HOME"], "/tmp/h")
        self.assertEqual(result["TERM"], "xterm-256color")

    def test_slow_shell_times_out_to_whitelist_with_warning(self):
        shell = write_shell(SLOW_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell, "CALLER_ONLY": "1", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}):
            t0 = time.time()
            result, warnings = env.build(extra={}, timeout=0.5, **IDENTITY)
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(len(warnings), 1)
        self.assertIn("login shell", warnings[0])
        self.assertNotIn("CALLER_ONLY", result)
        self.assertEqual(result["PATH"], "/usr/bin:/bin")
        self.assertEqual(result["LC_ALL"], "C")
        self.assertEqual(result["CORRAL_NAME"], "demo/alice")

    def test_broken_shell_falls_back_with_warning(self):
        shell = write_shell(BROKEN_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell}):
            result, warnings = env.build(extra={}, **IDENTITY)
        self.assertEqual(len(warnings), 1)
        self.assertIn("HOME", result)

    def test_slow_shell_process_is_killed(self):
        shell = write_shell(SLOW_RC)
        with mock.patch.dict(os.environ, {"SHELL": shell}):
            env.build(extra={}, timeout=0.3, **IDENTITY)
        import subprocess
        left = subprocess.run(["pgrep", "-f", shell], capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "")


class StartEnvIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.home = support.short_tmpdir()
        self.addCleanup(support.kill_all, self.home)

    def test_agent_sees_rebuilt_env(self):
        shell = write_shell(NOISY_RC)
        code, out = support.run_cli(
            ["start", "demo/alice", "--cwd", self.home, "--env", "EXTRA=1", "--",
             "sh", "-c", "env > env.txt; sleep 30"],
            home=self.home, env={"SHELL": shell, "CALLER_ONLY": "1", "CODEX_THREAD_ID": "caller"})
        self.assertEqual(code, errors.EXIT_OK, out)
        self.assertNotIn("warnings", out)
        path = os.path.join(self.home, "env.txt")

        def content():
            if os.path.exists(path):
                with open(path) as f:
                    text = f.read()
                return text if "CORRAL_INSTANCE" in text else ""
            return ""
        text = support.wait_until(content)
        lines = set(text.splitlines())
        self.assertIn("FOO=bar", lines)
        self.assertIn("EXTRA=1", lines)
        self.assertIn(f"CORRAL_INSTANCE={out['instance']}", lines)
        self.assertFalse(any(l.startswith(("CALLER_ONLY=", "CODEX_THREAD_ID=", "TMUX=")) for l in lines))

    def test_start_reports_warning_on_fallback(self):
        shell = write_shell(BROKEN_RC)
        code, out = support.run_cli(["start", "demo/alice", "--cwd", self.home, "--", "sleep", "30"],
                                    home=self.home, env={"SHELL": shell})
        self.assertEqual(code, errors.EXIT_OK, out)
        self.assertEqual(len(out["warnings"]), 1)


if __name__ == "__main__":
    unittest.main()
