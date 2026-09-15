"""M7：契约文档和实现一致；corral guide；按使用说明走一遍委派流程。"""
import os
import re
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase
from corral import cli, errors, events

CONTRACT = os.path.join(support.ROOT, "docs", "CONTRACT.md")
GUIDE = os.path.join(support.SRC, "corral", "AGENT_USAGE.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def ticks(line):
    return re.findall(r"`([^`]+)`", line)


def command_sections(doc):
    """### `corral xxx` 小节 → {标签: [反引号里的词]}"""
    sections = {}
    for block in re.split(r"^### ", doc, flags=re.M)[1:]:
        m = re.match(r"`corral (\w+)`", block)
        if not m:
            continue
        info = {}
        for line in block.splitlines():
            label, sep, rest = line.partition("：")
            if sep and label.strip("- ") in ("选项", "输出字段", "每项字段"):
                info[label.strip("- ")] = ticks(rest)
        sections[m.group(1)] = info
    return sections


def listed(doc, label):
    for line in doc.splitlines():
        if line.startswith(f"- {label}："):
            return ticks(line)
    raise AssertionError(f"contract has no line for {label}")


class ContractDocTest(unittest.TestCase):
    def setUp(self):
        self.doc = read(CONTRACT)
        self.sections = command_sections(self.doc)
        self.parser_commands = {}
        for action in cli.build_parser()._subparsers._group_actions:
            for name, sub in action.choices.items():
                opts = {o for a in sub._actions for o in a.option_strings if o.startswith("--") and o != "--help"}
                self.parser_commands[name] = opts

    def test_every_command_documented_with_its_options(self):
        self.assertEqual(set(self.sections), set(self.parser_commands))
        for name, opts in self.parser_commands.items():
            with self.subTest(command=name):
                self.assertEqual(set(self.sections[name].get("选项", [])), opts)

    def test_value_lists(self):
        self.assertEqual(set(listed(self.doc, "状态值")), set(events.STATES) | {"unknown"})
        self.assertEqual(set(listed(self.doc, "wait 结果值")), {"idle", "blocked", "stopped-quiet", "unknown"})
        self.assertEqual(set(listed(self.doc, "输入来源值")), {"send", "human", "agent"})
        self.assertEqual(set(listed(self.doc, "按键名")), set(cli.KEYS))
        self.assertEqual(listed(self.doc, "契约版本"), [errors.CONTRACT_VERSION])


class ContractOutputTest(AgentTestCase):
    def fields(self, command):
        return set(command_sections(read(CONTRACT))[command]["输出字段"])

    def assertDocumented(self, command, out, label="输出字段"):
        documented = set(command_sections(read(CONTRACT))[command][label])
        self.assertLessEqual(set(out), documented, f"{command}: undocumented {set(out) - documented}")

    def test_outputs_only_use_documented_fields(self):
        code, out = support.run_cli(["start", "demo/c", "--cwd", self.home, "--", support.fake_agent("claude")],
                                    home=self.home)
        self.assertDocumented("start", out)
        self.states_until("demo/c", lambda s: s.get("state") == "idle")
        self.assertDocumented("status", self.status("demo/c"))
        self.assertDocumented("send", self.cli("send", "demo/c", "reply:hi")[1])
        self.assertDocumented("wait", self.cli("wait", "demo/c")[1])
        self.assertDocumented("reply", self.cli("reply", "demo/c")[1])
        self.assertDocumented("keys", self.cli("keys", "demo/c", "up")[1])
        self.assertDocumented("where", self.cli("where", "demo/c")[1])
        ls = self.cli("ls")[1]
        self.assertDocumented("ls", ls)
        for entry in ls["agents"]:
            self.assertDocumented("ls", entry, label="每项字段")
        self.assertDocumented("stop", self.cli("stop", "demo/c")[1])


class GuideTest(unittest.TestCase):
    def test_guide_prints_usage_doc(self):
        code, out = support.run_cli(["guide"])
        self.assertEqual(code, 0)
        self.assertEqual(out, read(GUIDE))

    def test_guide_commands_parse(self):
        parser = cli.build_parser()
        lines = [l.strip() for block in re.findall(r"```(?:sh)?\n(.*?)```", read(GUIDE), re.S)
                 for l in block.splitlines() if l.strip().startswith("corral ")]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                argv = re.sub(r"<[^>]+>", "x", line).split()[1:]
                own, agent = cli._split_agent_command(argv)
                parser.parse_args(own)


class GuideWalkthroughTest(AgentTestCase):
    def test_delegation_flow_from_guide(self):
        # AGENT_USAGE.md 里的临时委派：start --unique --prompt → wait → reply → send → wait → reply → stop
        code, out = support.run_cli(["start", "demo/ask", "--unique", "--cwd", self.home, "--prompt", "reply:first",
                                     "--", support.fake_agent("codex")], home=self.home)
        self.assertEqual(code, 0, out)
        name = out["name"]
        self.assertEqual(name, "demo/ask-1")
        self.assertEqual(self.cli("wait", name)[1]["result"], "idle")
        self.assertEqual(self.cli("reply", name)[1]["text"], "first")
        self.assertTrue(self.cli("send", name, "reply:second")[1]["confirmed"])
        self.assertEqual(self.cli("wait", name)[1]["result"], "idle")
        self.assertEqual(self.cli("reply", name)[1]["text"], "second")
        self.assertEqual(self.cli("stop", name)[0], 0)


if __name__ == "__main__":
    unittest.main()
