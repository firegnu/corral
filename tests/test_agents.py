"""M4：适配器——按 agent 种类拼启动参数（钩子、首句）。"""
import json
import shlex
import sys
import tomllib
import unittest

from tests import support
from corral import agents

HOOK_PATH = "/tmp/h/demo/a b'c/中/hook.py"


class LookupTest(unittest.TestCase):
    def test_by_basename(self):
        self.assertEqual(agents.for_command(["claude"]).kind, "claude")
        self.assertEqual(agents.for_command(["/opt/bin/claude", "--model", "haiku"]).kind, "claude")
        self.assertEqual(agents.for_command(["codex"]).kind, "codex")
        self.assertEqual(agents.for_command(["/Users/x/.bun/bin/pi"]).kind, "pi")
        self.assertEqual(agents.for_command(["/opt/homebrew/bin/omp"]).kind, "omp")
        self.assertIsNone(agents.for_command(["sh", "-c", "x"]))


class HookCommandTest(unittest.TestCase):
    def test_runs_copy_with_system_python(self):
        cmd = agents.hook_command(HOOK_PATH, "Stop")
        self.assertEqual(shlex.split(cmd), ["/usr/bin/python3", "-I", "-S", HOOK_PATH, "Stop"])
        self.assertNotIn(support.ROOT, cmd)
        self.assertNotIn(sys.executable, cmd)


class ClaudeTest(unittest.TestCase):
    def build(self, argv, prompt=None):
        return agents.for_command(argv).build(argv, HOOK_PATH, prompt)

    def test_settings_hooks(self):
        out = self.build(["claude", "--model", "haiku"])
        self.assertEqual(out[0], "claude")
        self.assertEqual(out[1], "--settings")
        hooks = json.loads(out[2])["hooks"]
        self.assertEqual(set(hooks), set(agents.CLAUDE_EVENTS))
        for ev, entries in hooks.items():
            [entry] = entries
            [h] = entry["hooks"]
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["command"], agents.hook_command(HOOK_PATH, ev))
            if ev in ("PreToolUse", "PostToolUse", "PermissionRequest"):
                self.assertEqual(entry["matcher"], "*")
        self.assertEqual(out[3:], ["--model", "haiku"])

    def test_prompt_is_last_argument(self):
        out = self.build(["claude", "--model", "haiku"], prompt="first line\nsecond")
        self.assertEqual(out[-2:], ["--", "first line\nsecond"])  # -- 防止被多值选项吞掉
        self.assertEqual(out[-4:-2], ["--model", "haiku"])


class CodexTest(unittest.TestCase):
    def build(self, argv, prompt=None):
        return agents.for_command(argv).build(argv, HOOK_PATH, prompt)

    def test_config_hooks_parse_as_toml_and_bypass_trust(self):
        out = self.build(["codex", "--yolo", "-m", "gpt-5.6-luna"])
        self.assertEqual(out[0], "codex")
        self.assertIn("--dangerously-bypass-hook-trust", out)
        overrides = [out[i + 1] for i, a in enumerate(out) if a == "-c"]
        parsed = {}
        for o in overrides:
            parsed.update(tomllib.loads(o)["hooks"])
        self.assertEqual(set(parsed), set(agents.CODEX_EVENTS))
        for ev, entries in parsed.items():
            [entry] = entries
            [h] = entry["hooks"]
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["command"], agents.hook_command(HOOK_PATH, ev))
            self.assertLessEqual(h["timeout"], 3 if ev == "Interrupt" else 10)
        self.assertEqual(out[-3:], ["--yolo", "-m", "gpt-5.6-luna"])

    def test_prompt_is_last_argument(self):
        out = self.build(["codex", "--yolo"], prompt="hello")
        self.assertEqual(out[-3:], ["--yolo", "--", "hello"])


class PiTest(unittest.TestCase):
    HOOK = "/tmp/h/demo/p/hook_pi.ts"

    def build(self, argv, prompt=None):
        return agents.for_command(argv).build(argv, self.HOOK, prompt)

    def test_extension_flag_loads_private_copy(self):
        out = self.build(["pi", "--model", "sonnet"])
        self.assertEqual(out, ["pi", "--extension", self.HOOK, "--model", "sonnet"])

    def test_prompt_is_last_argument(self):
        out = self.build(["pi", "--model", "sonnet"], prompt="-first\nsecond")
        self.assertEqual(out[-2:], ["--", "-first\nsecond"])

    def test_hook_file_and_quit(self):
        pi = agents.ADAPTERS["pi"]
        self.assertEqual(pi.hook_file, "hook_pi.ts")
        self.assertFalse(pi.needs_hook_python)
        self.assertEqual([st.get("signal") for st in agents.quit_steps("pi")], ["HUP", "TERM"])
        for kind in ("claude", "codex"):
            self.assertEqual((agents.ADAPTERS[kind].hook_file, agents.ADAPTERS[kind].needs_hook_python),
                             ("hook.py", True))


class OmpTest(unittest.TestCase):
    HOOK = "/tmp/h/demo/o/hook_omp.ts"

    def build(self, argv, prompt=None):
        return agents.for_command(argv).build(argv, self.HOOK, prompt)

    def test_extension_flag_loads_private_copy(self):
        out = self.build(["omp", "--approval-mode", "yolo"])
        self.assertEqual(out, ["omp", "--extension", self.HOOK, "--approval-mode", "yolo"])

    def test_prompt_is_last_argument(self):
        self.assertEqual(self.build(["omp"], prompt="-hi")[-2:], ["--", "-hi"])

    def test_hook_file_and_quit(self):
        omp = agents.ADAPTERS["omp"]
        self.assertEqual((omp.hook_file, omp.needs_hook_python), ("hook_omp.ts", False))
        self.assertEqual([st.get("signal") for st in agents.quit_steps("omp")], ["HUP", "TERM"])


if __name__ == "__main__":
    unittest.main()
