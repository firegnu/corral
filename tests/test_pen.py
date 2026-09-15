"""M1：栏位 + start / ls / where / read / status（终端层字段）。被测 agent 是普通 shell 命令。"""
import ast
import os
import signal
import stat
import subprocess
import unittest

from tests import support
from corral import errors, protocol


class PenTestCase(unittest.TestCase):
    def setUp(self):
        self.home = support.short_tmpdir()
        self.addCleanup(support.kill_all, self.home)

    def cli(self, *args, **kw):
        return support.run_cli(list(args), home=self.home, **kw)

    def start(self, name, *cmd, **kw):
        code, out = self.cli("start", name, "--cwd", self.home, "--", *cmd, **kw)
        self.assertEqual(code, errors.EXIT_OK, out)
        return out

    def alive(self, name):
        return self.cli("status", name)[0] == errors.EXIT_OK

    def gone(self, name):
        return self.cli("status", name)[0] == errors.EXIT_NOT_FOUND


class StartTest(PenTestCase):
    def test_start_reports_name_and_instance(self):
        out = self.start("demo/alice", "sleep", "30")
        self.assertTrue(out["ok"])
        self.assertEqual(out["name"], "demo/alice")
        self.assertRegex(out["instance"], r"^[0-9a-f]{12}$")
        self.assertEqual(out["kind"], "sleep")

    def test_same_name_twice_is_exists(self):
        self.start("demo/alice", "sleep", "30")
        code, out = self.cli("start", "demo/alice", "--", "sleep", "30")
        self.assertEqual(code, errors.EXIT_EXISTS)
        self.assertEqual(out["error"], "exists")

    def test_unique_appends_free_suffix(self):
        code1, out1 = self.cli("start", "demo/ask", "--unique", "--cwd", self.home, "--", "sleep", "30")
        code2, out2 = self.cli("start", "demo/ask", "--unique", "--cwd", self.home, "--", "sleep", "30")
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual((out1["name"], out2["name"]), ("demo/ask-1", "demo/ask-2"))

    def test_pen_outlives_start_and_is_detached(self):
        self.start("demo/alice", "sleep", "30")
        meta = support.read_meta(self.home, "demo/alice")
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(meta["pen_pid"])],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(ppid, "1")
        self.assertNotEqual(os.getsid(meta["pen_pid"]), os.getsid(0))
        self.assertTrue(self.alive("demo/alice"))

    def test_cwd_is_used(self):
        self.start("demo/alice", "sh", "-c", "pwd > where.txt; sleep 30")
        path = os.path.join(self.home, "where.txt")
        def content():
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip()
            return ""
        self.assertTrue(support.wait_until(content))
        self.assertEqual(os.path.realpath(content()), os.path.realpath(self.home))

    def test_bad_cwd(self):
        code, out = self.cli("start", "demo/alice", "--cwd", "/nonexistent/dir", "--", "sleep", "1")
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "bad_cwd")

    def test_exec_failure_is_reported_and_releases_name(self):
        code, out = self.cli("start", "demo/alice", "--", "no-such-command-xyz")
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "exec_failed")
        self.start("demo/alice", "sleep", "30")

    def test_socket_path_too_long_rejected_before_creating_dirs(self):
        home = "/tmp/" + "h" * 80
        self.addCleanup(lambda: os.path.isdir(home) and os.rmdir(home))
        code, out = support.run_cli(["start", "demo/alice-with-a-long-name", "--", "sleep", "1"], home=home)
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "path_too_long")
        self.assertFalse(os.path.exists(home))


class LifecycleTest(PenTestCase):
    def test_agent_exit_code_recorded(self):
        self.start("demo/alice", "sh", "-c", "sleep 0.3; exit 7")
        self.assertTrue(support.wait_until(lambda: self.gone("demo/alice")))
        code, out = self.cli("status", "demo/alice")
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.assertEqual(out["exited"]["code"], 7)

    def test_not_found(self):
        code, out = self.cli("status", "demo/nobody")
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.assertEqual(out["error"], "not_found")

    def test_killed_pen_is_cleaned_by_ls(self):
        self.start("demo/alice", "sleep", "30")
        self.start("demo/bob", "sleep", "30")
        meta = support.read_meta(self.home, "demo/alice")
        os.kill(meta["pen_pid"], signal.SIGKILL)
        self.assertTrue(support.wait_until(lambda: self.gone("demo/alice")))
        code, out = self.cli("ls")
        self.assertEqual(code, 0)
        self.assertEqual([a["name"] for a in out["agents"]], ["demo/bob"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "demo", "alice")))

    def test_agent_dies_with_killed_pen(self):
        self.start("demo/alice", "sleep", "30")
        meta = support.read_meta(self.home, "demo/alice")
        os.kill(meta["pen_pid"], signal.SIGKILL)

        def agent_gone():
            try:
                os.kill(meta["agent_pid"], 0)
            except ProcessLookupError:
                return True
            return False
        self.assertTrue(support.wait_until(agent_gone))

    def test_agent_signals_are_default_not_inherited_from_python(self):
        # Python 自己忽略 SIGPIPE，栏位忽略 SIGHUP；这些「忽略」会随 exec 继承，agent 必须拿到默认处理
        for sig, num in (("PIPE", signal.SIGPIPE), ("HUP", signal.SIGHUP)):
            with self.subTest(sig=sig):
                name = f"demo/sig-{sig.lower()}"
                self.start(name, "sh", "-c", f"sleep 0.3; kill -{sig} $$; exit 0")
                self.assertTrue(support.wait_until(lambda: self.gone(name)))
                self.assertEqual(self.cli("status", name)[1]["exited"]["code"], -num)


class QueryTest(PenTestCase):
    def test_where(self):
        out = self.start("demo/alice", "sleep", "30")
        code, w = self.cli("where", "demo/alice")
        self.assertEqual(code, 0)
        self.assertEqual(w["instance"], out["instance"])
        self.assertEqual(os.path.realpath(w["cwd"]), os.path.realpath(self.home))
        self.assertEqual(w["kind"], "sleep")
        self.assertIsInstance(w["agent_pid"], int)

    def test_status_terminal_fields(self):
        self.start("demo/alice", "sh", "-c", r"printf '\033]0;my-title\007hello'; sleep 30")
        self.assertTrue(support.wait_until(lambda: self.cli("status", "demo/alice")[1].get("title") == "my-title"))
        code, st = self.cli("status", "demo/alice")
        self.assertEqual(code, 0)
        self.assertEqual(st["state"], "unknown")  # 不认识的 agent
        self.assertIsInstance(st["last_output"], float)
        self.assertGreaterEqual(st["idle_for"], 0)
        self.assertEqual(st["attached"], 0)
        self.assertEqual(st["proto"], protocol.PROTOCOL_VERSION)

    def test_read_strips_control_sequences(self):
        self.start("demo/alice", "sh", "-c", r"printf 'hello \033[31mred\033[0m\n'; sleep 30")
        self.assertTrue(support.wait_until(lambda: "hello red" in self.cli("read", "demo/alice")[1]))

    def test_ls_lists_running(self):
        self.start("demo/alice", "sleep", "30")
        self.start("demo/bob", "sleep", "30")
        code, out = self.cli("ls")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(a["name"] for a in out["agents"]), ["demo/alice", "demo/bob"])


class PermissionTest(PenTestCase):
    def mode(self, *parts):
        return stat.S_IMODE(os.stat(os.path.join(self.home, *parts)).st_mode)

    def test_modes_do_not_depend_on_umask(self):
        os.rmdir(self.home)  # 让 corral 自己创建状态目录
        code, out = self.cli("start", "demo/alice", "--cwd", "/tmp", "--", "sh", "-c", "sleep 0.5; exit 0", umask=0)
        self.assertEqual(code, errors.EXIT_OK, out)
        for parts in ((), ("demo",), ("demo", "alice")):
            self.assertEqual(self.mode(*parts), 0o700, parts)
        for f in ("lock", "meta.json", "sock", "pen.log"):
            self.assertEqual(self.mode("demo", "alice", f), 0o600, f)
        self.assertTrue(support.wait_until(lambda: os.path.exists(os.path.join(self.home, "demo", "alice", "exit.json"))))
        self.assertEqual(self.mode("demo", "alice", "exit.json"), 0o600)


class ProtocolVersionTest(PenTestCase):
    def test_version_in_meta_and_handshake(self):
        self.start("demo/alice", "sleep", "30")
        self.assertEqual(support.read_meta(self.home, "demo/alice")["proto"], protocol.PROTOCOL_VERSION)
        self.assertEqual(self.cli("status", "demo/alice")[1]["proto"], protocol.PROTOCOL_VERSION)


class PenImportsTest(unittest.TestCase):
    def test_pen_has_no_lazy_imports(self):
        # 栏位启动后不能再从磁盘加载 corral 的代码：pen.py 不允许在函数里 import
        path = os.path.join(support.SRC, "corral", "pen.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    self.assertNotIsInstance(inner, (ast.Import, ast.ImportFrom),
                                             f"import inside {node.name}() in pen.py")


if __name__ == "__main__":
    unittest.main()
