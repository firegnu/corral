"""M4：钩子脚本。由 /usr/bin/python3 运行（Python 3.9 兼容），只用 json/os/sys/time，不往标准输出写。"""
import ast
import json
import os
import stat
import subprocess
import unittest

from tests import support

HOOK = os.path.join(support.SRC, "corral", "hook.py")
SYSTEM_PYTHON = "/usr/bin/python3"


def run_hook(event, stdin, env_extra=None, python=SYSTEM_PYTHON):
    env = {"PATH": "/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run([python, "-I", "-S", HOOK, event], input=stdin, capture_output=True, env=env, timeout=10)


@unittest.skipUnless(os.path.exists(SYSTEM_PYTHON), "no /usr/bin/python3")
class HookTest(unittest.TestCase):
    def setUp(self):
        self.dir = support.short_tmpdir()
        self.events = os.path.join(self.dir, "events")
        fd = os.open(self.events, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        self.env = {"CORRAL_EVENTS": self.events, "CORRAL_INSTANCE": "0123456789ab"}

    def lines(self):
        with open(self.events, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_writes_selected_fields_and_nothing_to_stdout(self):
        payload = {"session_id": "s1", "cwd": "/w", "transcript_path": "/t.jsonl", "hook_event_name": "Stop",
                   "last_assistant_message": "多行\n回复", "tool_input": {"content": "x" * 10000},
                   "tool_response": "big", "prompt": "hi", "source": "startup", "tool_name": "Bash",
                   "notification_type": "permission_prompt"}
        proc = run_hook("Stop", json.dumps(payload).encode(), self.env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        [line] = self.lines()
        self.assertEqual(line["v"], 1)
        self.assertEqual(line["ev"], "Stop")
        self.assertEqual(line["inst"], "0123456789ab")
        self.assertIsInstance(line["t"], float)
        self.assertTrue(line["has_transcript"])
        for k in ("session_id", "cwd", "last_assistant_message", "prompt", "source", "tool_name",
                  "notification_type"):
            self.assertEqual(line[k], payload[k])
        self.assertNotIn("tool_input", line)
        self.assertNotIn("tool_response", line)
        self.assertNotIn("transcript_path", line)

    def test_malformed_input_still_exits_zero_silently(self):
        for stdin in (b"not json", b"", b"[1,2]", b"\xff\xfe"):
            with self.subTest(stdin=stdin):
                proc = run_hook("Stop", stdin, self.env)
                self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertEqual(len(self.lines()), 4)
        self.assertFalse(self.lines()[0]["has_transcript"])

    def test_no_events_env_does_nothing(self):
        proc = run_hook("Stop", b"{}", {})
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))

    def test_unwritable_events_file_still_exits_zero(self):
        os.chmod(self.events, 0)
        self.addCleanup(os.chmod, self.events, stat.S_IRUSR | stat.S_IWUSR)
        proc = run_hook("Stop", b"{}", self.env)
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))

    def test_compiles_on_system_python(self):
        proc = subprocess.run([SYSTEM_PYTHON, "-I", "-S", "-c",
                               f"import ast,sys; ast.parse(open({HOOK!r}).read(), feature_version=(3, 9))"],
                              capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class HookSourceTest(unittest.TestCase):
    def test_only_allowed_imports(self):
        with open(HOOK, encoding="utf-8") as f:
            tree = ast.parse(f.read(), feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertLessEqual(imported, {"json", "os", "sys", "time"})

    def test_no_print_calls(self):
        with open(HOOK, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                self.fail("hook.py must not print")


if __name__ == "__main__":
    unittest.main()
