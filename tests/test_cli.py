import os
import re
import unittest

from tests import support
from corral import errors

SANDBOX_ENV = {"CODEX_SANDBOX": "seatbelt"}

# 每个会碰栏位或状态目录的命令，给一组最小参数
AGENT_COMMANDS = [
    ["start", "demo/a", "--", "sh"],
    ["send", "demo/a", "hi"],
    ["keys", "demo/a", "enter"],
    ["status", "demo/a"],
    ["wait", "demo/a"],
    ["reply", "demo/a"],
    ["where", "demo/a"],
    ["ls"],
    ["read", "demo/a"],
    ["attach", "demo/a"],
    ["stop", "demo/a"],
]


class SandboxTest(unittest.TestCase):
    def test_every_agent_command_refused_in_sandbox(self):
        home = support.short_tmpdir()
        for args in AGENT_COMMANDS:
            with self.subTest(command=args[0]):
                code, out = support.run_cli(args, env=SANDBOX_ENV, home=home)
                self.assertEqual(code, errors.EXIT_SANDBOX)
                self.assertIsInstance(out, dict)
                self.assertFalse(out["ok"])
                self.assertEqual(out["error"], "sandbox")
        # 拒绝时不碰状态目录
        self.assertEqual(os.listdir(home), [])

    def test_sandbox_checked_before_name_validation(self):
        code, out = support.run_cli(["status", "../bad"], env=SANDBOX_ENV, home=support.short_tmpdir())
        self.assertEqual(code, errors.EXIT_SANDBOX)

    def test_guide_and_version_not_refused(self):
        for args in (["guide"], ["--version"]):
            with self.subTest(args=args):
                code, _ = support.run_cli(args, env=SANDBOX_ENV)
                self.assertEqual(code, errors.EXIT_OK)


class UsageTest(unittest.TestCase):
    def test_unknown_command_is_usage_error_json(self):
        code, out = support.run_cli(["nope"])
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "usage")

    def test_missing_argument_is_usage_error_not_argparse_exit_2(self):
        # argparse 默认用退出码 2，和「不存在」冲突
        code, out = support.run_cli(["status"])
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "usage")

    def test_bad_name_is_error_json(self):
        code, out = support.run_cli(["status", "../x"], home=support.short_tmpdir())
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out["error"], "bad_name")

    @unittest.skipUnless(os.path.exists("/usr/bin/python3"), "no /usr/bin/python3")
    def test_old_python_gets_clear_error(self):
        import subprocess
        proc = subprocess.run(["/usr/bin/python3", "-c", "import sys; print(sys.version_info >= (3, 11))"],
                              capture_output=True, text=True)
        if proc.stdout.strip() != "False":
            self.skipTest("/usr/bin/python3 is new enough")
        proc = subprocess.run(["/usr/bin/python3", support.BIN, "ls"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, errors.EXIT_ERROR)
        self.assertIn('"python_too_old"', proc.stdout)

    def test_version(self):
        code, out = support.run_cli(["--version"])
        self.assertEqual(code, errors.EXIT_OK)
        self.assertTrue(out["ok"])
        self.assertRegex(out["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(out["contract"], errors.CONTRACT_VERSION)


class ContractExitCodesTest(unittest.TestCase):
    def test_exit_code_table_matches_contract_doc(self):
        with open(os.path.join(support.ROOT, "docs", "CONTRACT.md"), encoding="utf-8") as f:
            doc = f.read()
        section = doc.split("## 退出码", 1)[1].split("\n## ", 1)[0]
        rows = dict((int(c), ident) for c, ident in re.findall(r"^\| (\d+) \| `([a-z_]+)` \|", section, re.M))
        self.assertEqual(rows, errors.EXIT_NAMES)


if __name__ == "__main__":
    unittest.main()
