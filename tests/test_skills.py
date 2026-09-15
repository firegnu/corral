"""M7b：agent skill 的内容核对；install-skills 的确认、写入、删除（全部在临时目录里）。"""
import json
import os
import pty
import re
import select
import sys
import time
import unittest

from tests import support
from tests.test_contract import command_sections, read, CONTRACT
from corral import cli, errors

SKILL = os.path.join(support.SRC, "corral", "skill", "SKILL.md")
FORBIDDEN = ("herdr", "herdsman", "moshi", "orca", "tmux")


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields, text[m.end():]


class SkillContentTest(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL)
        self.meta, self.body = frontmatter(self.text)

    def test_frontmatter_and_triggers(self):
        self.assertEqual(self.meta["name"], "corral")
        desc = self.meta["description"]
        for phrase in ("开一个 Claude Code", "Codex", "交给另一个 agent", "delegate to another agent"):
            self.assertIn(phrase, desc)
        self.assertEqual(set(self.meta), {"name", "description"})

    def test_short_and_points_to_guide(self):
        self.assertLessEqual(len(self.text.splitlines()), 80)
        self.assertIn("corral guide", self.body)
        self.assertIn("command -v corral", self.body)
        self.assertIn(cli_marker(), self.text)

    def test_commands_parse(self):
        parser = cli.build_parser()
        lines = [l.strip() for block in re.findall(r"```(?:sh)?\n(.*?)```", self.body, re.S)
                 for l in block.splitlines() if l.strip().startswith("corral ")]
        self.assertTrue(any(l.startswith("corral start") and "--unique" in l and "--prompt" in l for l in lines))
        self.assertTrue(any(l.startswith("corral wait") and "--timeout" in l and "--quiet" in l for l in lines))
        for cmd in ("reply", "stop"):
            self.assertTrue(any(l.startswith(f"corral {cmd}") for l in lines), cmd)
        for line in lines:
            with self.subTest(line=line):
                argv = re.sub(r"<[^>]+>", "x", line).split()[1:]
                own, _ = cli._split_agent_command(argv)
                parser.parse_args(own)

    def test_exit_codes_match_errors_table(self):
        rows = re.findall(r"^\| (\d+) \| `([a-z_]+)` \|", self.body, re.M)
        self.assertTrue(rows)
        for code, ident in rows:
            self.assertEqual(errors.EXIT_NAMES[int(code)], ident)
        self.assertLessEqual({"3", "4", "6", "7", "8"}, {c for c, _ in rows})

    def test_neutral_wording(self):
        lowered = self.text.lower()
        for word in FORBIDDEN:
            self.assertNotIn(word, lowered)


def cli_marker():
    from corral import skills
    return skills.MARKER


class InstallSkillsTest(unittest.TestCase):
    def setUp(self):
        self.root = support.short_tmpdir()
        self.claude = os.path.join(self.root, "claude-config")
        self.env = {"CLAUDE_CONFIG_DIR": self.claude, "HOME": self.root, "CODEX_HOME": os.path.join(self.root, "ignored"),
                    "PATH": "/usr/bin:/bin"}
        self.claude_skill = os.path.join(self.claude, "skills", "corral", "SKILL.md")
        # Codex 按官方文档读 ~/.agents/skills；CODEX_HOME 不影响这个位置
        self.codex_skill = os.path.join(self.root, ".agents", "skills", "corral", "SKILL.md")

    def run_install(self, *args, env=None):
        return support.run_cli(["install-skills", *args], env={**self.env, **(env or {})})

    def files_under_root(self):
        found = []
        for d, _, files in os.walk(self.root):
            found += [os.path.relpath(os.path.join(d, f), self.root) for f in files]
        return sorted(found)

    def test_non_tty_without_yes_refuses_and_lists(self):
        code, out = self.run_install()
        self.assertEqual((code, out["error"]), (errors.EXIT_ERROR, "confirmation_required"))
        self.assertEqual({(i["agent"], i["status"]) for i in out["items"]}, {("claude", "create"), ("codex", "create")})
        self.assertEqual({i["path"] for i in out["items"]}, {self.claude_skill, self.codex_skill})
        self.assertEqual(self.files_under_root(), [])

    def test_dry_run_writes_nothing(self):
        code, out = self.run_install("--dry-run")
        self.assertEqual((code, out["dry_run"], out["written"]), (0, True, False))
        self.assertEqual(self.files_under_root(), [])

    def test_yes_writes_exactly_two_files(self):
        code, out = self.run_install("--yes")
        self.assertEqual((code, out["written"]), (0, True))
        self.assertEqual(self.files_under_root(), [".agents/skills/corral/SKILL.md",
                                                   "claude-config/skills/corral/SKILL.md"])
        for path in (self.claude_skill, self.codex_skill):
            self.assertEqual(read(path), read(SKILL))
        code, out = self.run_install("--yes")
        self.assertEqual({i["status"] for i in out["items"]}, {"same"})

    def test_changed_file_is_listed_as_overwrite(self):
        self.run_install("--yes")
        with open(self.codex_skill, "a") as f:
            f.write("\nlocal edit\n")
        code, out = self.run_install("--dry-run")
        status = {i["agent"]: i["status"] for i in out["items"]}
        self.assertEqual(status, {"claude": "same", "codex": "overwrite"})

    def test_target_and_neighbors_untouched(self):
        other = os.path.join(self.claude, "skills", "someone-else", "SKILL.md")
        os.makedirs(os.path.dirname(other))
        with open(other, "w") as f:
            f.write("keep me")
        code, out = self.run_install("--target", "claude", "--yes")
        self.assertEqual([i["agent"] for i in out["items"]], ["claude"])
        self.assertEqual(self.files_under_root(), ["claude-config/skills/corral/SKILL.md",
                                                   "claude-config/skills/someone-else/SKILL.md"])
        self.assertEqual(read(other), "keep me")

    def test_remove_only_marked_files(self):
        self.run_install("--yes")
        with open(self.codex_skill, "w") as f:
            f.write("---\nname: corral\ndescription: someone else's file\n---\n")
        code, out = self.run_install("--remove", "--yes")
        self.assertEqual(code, 0, out)
        status = {i["agent"]: i["status"] for i in out["items"]}
        self.assertEqual(status, {"claude": "remove", "codex": "foreign"})
        self.assertFalse(os.path.exists(os.path.dirname(self.claude_skill)))
        self.assertTrue(os.path.exists(self.codex_skill))
        self.assertTrue(out["warnings"])

    def test_sandbox_refused(self):
        code, out = self.run_install("--yes", env={"CODEX_SANDBOX": "seatbelt"})
        self.assertEqual(code, errors.EXIT_SANDBOX)
        self.assertEqual(self.files_under_root(), [])

    def test_warns_when_corral_not_on_path(self):
        code, out = self.run_install("--dry-run")
        self.assertTrue(any("PATH" in w for w in out["warnings"]))
        bindir = os.path.join(self.root, "bin")
        os.makedirs(bindir)
        os.symlink(support.BIN, os.path.join(bindir, "corral"))
        code, out = self.run_install("--dry-run", env={"PATH": f"{bindir}:/usr/bin:/bin"})
        self.assertFalse(any("PATH" in w for w in out["warnings"]))

    def test_project_level_writes_only_project_paths(self):
        project = os.path.join(self.root, "work")
        os.makedirs(project)
        code, out = self.run_install("--project", project, "--yes")
        self.assertEqual((code, out["written"]), (0, True), out)
        self.assertEqual({i["path"] for i in out["items"]},
                         {os.path.join(project, ".claude", "skills", "corral", "SKILL.md"),
                          os.path.join(project, ".agents", "skills", "corral", "SKILL.md")})
        self.assertEqual(self.files_under_root(), ["work/.agents/skills/corral/SKILL.md",
                                                   "work/.claude/skills/corral/SKILL.md"])
        code, out = self.run_install("--project", project, "--target", "codex", "--remove", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.files_under_root(), ["work/.claude/skills/corral/SKILL.md"])

    def test_project_must_exist(self):
        code, out = self.run_install("--project", os.path.join(self.root, "missing"), "--dry-run")
        self.assertEqual((code, out["error"]), (errors.EXIT_ERROR, "bad_project"))

    def test_output_fields_documented(self):
        documented = set(command_sections(read(CONTRACT))["install-skills"]["输出字段"])
        item_fields = set(command_sections(read(CONTRACT))["install-skills"]["每项字段"])
        code, out = self.run_install("--dry-run")
        self.assertLessEqual(set(out), documented)
        for item in out["items"]:
            self.assertLessEqual(set(item), item_fields)

    def interactive(self, answer):
        env = dict(os.environ, **self.env, SHELL=support.fast_shell())
        env.pop("CODEX_SANDBOX", None)
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, support.BIN, "install-skills"], env)
        out = b""
        end = time.time() + 10
        while b"[y/N]" not in out and time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                out += os.read(fd, 4096)
        os.write(fd, answer + b"\r")
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    out += os.read(fd, 4096)
                except OSError:
                    pass  # 子进程退出后伪终端读到结尾
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                os.close(fd)
                return os.waitstatus_to_exitcode(status), out
        os.kill(pid, 9)
        self.fail(f"install-skills did not finish: {out!r}")

    def test_interactive_prompt_lists_paths_then_asks(self):
        code, out = self.interactive(b"n")
        self.assertIn(self.claude_skill.encode(), out)
        self.assertIn(self.codex_skill.encode(), out)
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(self.files_under_root(), [])
        code, out = self.interactive(b"y")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.files_under_root()), 2)


if __name__ == "__main__":
    unittest.main()
