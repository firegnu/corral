"""M3：attach / attach --wait 的集成测试。在伪终端里跑 bin/corral attach，模拟一个终端窗口。"""
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import unittest

from tests import support
from corral import errors


class Window:
    """一个「终端窗口」：伪终端里跑 corral attach。"""

    def __init__(self, home, *args, rows=30, cols=100):
        env = dict(os.environ, CORRAL_HOME=home, SHELL=support.fast_shell())
        env.pop("CODEX_SANDBOX", None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(sys.executable, [sys.executable, support.BIN, "attach", *args], env)
        self.resize(rows, cols)
        self.output = b""
        self.exit_code = None

    def resize(self, rows, cols):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def pump(self, secs=0.3):
        end = time.time() + secs
        while True:
            left = end - time.time()
            if left <= 0:
                return
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                return
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.output += chunk

    def type(self, data):
        os.write(self.fd, data)

    def wait_for_output(self, needle, timeout=10.0):
        end = time.time() + timeout
        while needle not in self.output and time.time() < end:
            self.pump(0.1)
        return needle in self.output

    def exited(self, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.05)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == self.pid:
                self.exit_code = os.waitstatus_to_exitcode(status)
                self.pump(0.1)
                return True
        return False

    def close(self):
        if self.exit_code is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass


class AttachTestCase(unittest.TestCase):
    def setUp(self):
        self.home = support.short_tmpdir()
        self.addCleanup(support.kill_all, self.home)

    def cli(self, *args):
        return support.run_cli(list(args), home=self.home)

    def start(self, name, script):
        code, out = self.cli("start", name, "--cwd", self.home, "--", "sh", "-c", script)
        self.assertEqual(code, errors.EXIT_OK, out)
        return out

    def window(self, *args, **kw):
        w = Window(self.home, *args, **kw)
        self.addCleanup(w.close)
        return w

    def status(self, name):
        return self.cli("status", name)[1]

    def file_text(self, fname):
        path = os.path.join(self.home, fname)
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            return f.read()


class AttachTest(AttachTestCase):
    def test_size_follows_writer_window(self):
        self.start("demo/a", 'trap "stty size > size.txt" WINCH; while :; do sleep 0.05; done')
        w = self.window("demo/a", rows=30, cols=100)
        self.assertTrue(support.wait_until(lambda: self.file_text("size.txt").strip() == "30 100"))
        w.resize(45, 150)
        self.assertTrue(support.wait_until(lambda: self.file_text("size.txt").strip() == "45 150"))

    def test_writer_types_readonly_ignored(self):
        self.start("demo/a", "cat > input.txt")
        w1 = self.window("demo/a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 1))
        w2 = self.window("demo/a", rows=20, cols=80)
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 2))
        w1.type(b"hello\r")
        w2.type(b"evil\r")
        time.sleep(0.3)
        w1.type(b"bye\r")
        self.assertTrue(support.wait_until(lambda: "bye" in self.file_text("input.txt")))
        self.assertIn("hello", self.file_text("input.txt"))
        self.assertNotIn("evil", self.file_text("input.txt"))

    def test_detach_keys(self):
        self.start("demo/a", "cat > /dev/null")
        for key in (b"\x1d", b"\x1b[93;5u"):
            with self.subTest(key=key):
                w = self.window("demo/a")
                self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 1))
                w.type(key)
                self.assertTrue(w.exited())
                self.assertEqual(w.exit_code, 0)
                self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 0))

    def test_key_release_does_not_detach(self):
        self.start("demo/a", "cat > /dev/null")
        w = self.window("demo/a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 1))
        w.type(b"\x1b[93;5:3u")
        self.assertFalse(w.exited(timeout=0.8))
        w.type(b"\x1d")
        self.assertTrue(w.exited())

    def test_modes_replayed_then_restored(self):
        self.start("demo/a", r'printf "\033[?2004h\033[>7u\033[?1049h"; cat > /dev/null')
        time.sleep(0.3)
        w = self.window("demo/a")
        self.assertTrue(w.wait_for_output(b"\x1b[>7u"))
        for part in (b"\x1b[?1049h", b"\x1b[?2004h", b"\x1b[22;0t", b"\x1b]2;demo/a\x07"):
            self.assertIn(part, w.output)
        w.type(b"\x1d")
        self.assertTrue(w.exited())
        tail = w.output[w.output.rfind(b"\x1b[>7u"):]
        for part in (b"\x1b[?2004l", b"\x1b[<1u", b"\x1b[?1049l", b"\x1b[23;0t"):
            self.assertIn(part, tail)

    def test_human_key_time_ignores_terminal_responses(self):
        self.start("demo/a", "cat > /dev/null")
        w = self.window("demo/a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 1))
        self.assertIsNone(self.status("demo/a")["last_human_input"])
        w.type(b"\x1b[I\x1b[O\x1b[5;10R\x1b]11;rgb:0000/0000/0000\x07")
        time.sleep(0.5)
        self.assertIsNone(self.status("demo/a")["last_human_input"])
        w.type(b"a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a")["last_human_input"] is not None))
        first = self.status("demo/a")["last_human_input"]
        time.sleep(0.1)
        w.type(b"\x1b[<0;10;5M")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a")["last_human_input"] > first))

    def test_readonly_window_input_is_not_human_input(self):
        self.start("demo/a", "cat > /dev/null")
        self.window("demo/a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 1))
        w2 = self.window("demo/a")
        self.assertTrue(support.wait_until(lambda: self.status("demo/a").get("attached") == 2))
        w2.type(b"x")
        time.sleep(0.5)
        self.assertIsNone(self.status("demo/a")["last_human_input"])

    def test_agent_exit_ends_attach(self):
        self.start("demo/a", r'printf "\033[?2004h"; sleep 1.5')
        w = self.window("demo/a")
        self.assertTrue(w.exited(timeout=10))
        self.assertEqual(w.exit_code, errors.EXIT_NOT_FOUND)
        self.assertIn(b"\x1b[?2004l", w.output)

    def test_not_found_and_not_a_tty(self):
        code, out = self.cli("attach", "demo/nobody")
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.start("demo/a", "cat > /dev/null")
        code, out = self.cli("attach", "demo/a")
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "not_a_tty")


class AttachWaitTest(AttachTestCase):
    def test_waits_attaches_and_returns_to_waiting(self):
        w = self.window("--wait", "demo/w")
        self.assertTrue(w.wait_for_output("waiting for demo/w".encode()))
        for round_ in (1, 2):
            with self.subTest(round=round_):
                count = w.output.count(b"waiting for demo/w")
                self.start("demo/w", "sleep 1.5")
                self.assertTrue(support.wait_until(lambda: self.status("demo/w").get("attached") == 1))
                self.assertTrue(support.wait_until(lambda: w.pump(0.1) or
                                                   w.output.count(b"waiting for demo/w") > count, timeout=10))
                self.assertFalse(w.exited(timeout=0.1))
        w.type(b"\x03")  # 等待时 Ctrl-C 退出
        self.assertTrue(w.exited())
        self.assertEqual(w.exit_code, 0)

    def test_detach_key_ends_wait_mode(self):
        self.start("demo/w", "cat > /dev/null")
        w = self.window("--wait", "demo/w")
        self.assertTrue(support.wait_until(lambda: self.status("demo/w").get("attached") == 1))
        w.type(b"\x1d")
        self.assertTrue(w.exited())
        self.assertEqual(w.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
