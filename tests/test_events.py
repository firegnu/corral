"""M4：事件文件增量读（cursor）、只认主会话、状态机、事件格式版本。"""
import json
import os
import stat
import subprocess
import sys
import time
import unittest

from tests import support
from corral import errors, events

INST = "0123456789ab"


class EventsTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = support.short_tmpdir()
        self.cwd = support.short_tmpdir()
        self.path = os.path.join(self.dir, "events")
        open(self.path, "w").close()
        self.t = time.time()

    def ev(self, name, sid="main", inst=INST, **fields):
        self.t += 0.01
        rec = {"v": 1, "t": self.t, "ev": name, "inst": inst, "session_id": sid, **fields}
        return json.dumps(rec, ensure_ascii=False) + "\n"

    def start_main(self, sid="main"):
        return self.ev("SessionStart", sid, has_transcript=True, cwd=self.cwd, source="startup")

    def append(self, *lines):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(lines))

    def read(self, instance=INST):
        return events.read(self.dir, instance, self.cwd)


class StateMachineTest(EventsTestCase):
    def test_starting_without_events(self):
        self.assertEqual(self.read()["state"], "starting")

    def test_basic_turn(self):
        self.append(self.start_main())
        self.assertEqual(self.read()["state"], "idle")
        self.append(self.ev("UserPromptSubmit", prompt="  hello\r\n"))
        snap = self.read()
        self.assertEqual(snap["state"], "working")
        self.assertIsNotNone(snap["turn_started"])
        self.assertEqual(snap["inputs"][-1]["digest"], events.digest("hello"))
        self.append(self.ev("PreToolUse", tool_name="Bash"))
        self.assertEqual((self.read()["state"], self.read()["last_tool"]), ("working", "Bash"))
        self.append(self.ev("Stop", last_assistant_message="done\n```py\nx\n```"))
        snap = self.read()
        self.assertEqual(snap["state"], "idle")
        self.assertEqual(snap["reply"], "done\n```py\nx\n```")
        self.assertEqual(snap["last_event"], "Stop")

    def test_blocked_and_back(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"),
                    self.ev("PreToolUse", tool_name="AskUserQuestion"),
                    self.ev("PermissionRequest", tool_name="AskUserQuestion"))
        self.assertEqual(self.read()["state"], "blocked")
        self.append(self.ev("PostToolUse", tool_name="AskUserQuestion"))
        self.assertEqual(self.read()["state"], "working")
        self.append(self.ev("Notification", notification_type="permission_prompt"))
        self.assertEqual(self.read()["state"], "blocked")

    def test_idle_prompt_notification_does_not_change_state(self):
        self.append(self.start_main(), self.ev("Notification", notification_type="idle_prompt"))
        self.assertEqual(self.read()["state"], "idle")

    def test_interrupt_event(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"), self.ev("Interrupt"))
        self.assertEqual(self.read()["state"], "idle")

    def test_compact_session_start_keeps_working(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"),
                    self.ev("SessionStart", has_transcript=True, cwd=self.cwd, source="compact"))
        self.assertEqual(self.read()["state"], "working")

    def test_subagent_session_ignored(self):
        other = support.short_tmpdir()
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"),
                    self.ev("Stop", last_assistant_message="main reply"),
                    self.ev("SessionStart", "memory", has_transcript=False, cwd=other, source="startup"),
                    self.ev("UserPromptSubmit", "memory", prompt="consolidate"),
                    self.ev("PreToolUse", "memory", tool_name="Bash"),
                    self.ev("Stop", "memory", last_assistant_message="memory reply"))
        snap = self.read()
        self.assertEqual((snap["state"], snap["reply"], snap["last_tool"]), ("idle", "main reply", None))

    def test_input_before_session_start_is_replayed(self):
        # Codex 第一次提交：两个钩子进程几乎同时写，输入事件可能先落盘
        self.append(self.ev("UserPromptSubmit", prompt="first"), self.start_main())
        snap = self.read()
        self.assertEqual(snap["state"], "working")
        self.assertEqual(snap["inputs"][-1]["digest"], events.digest("first"))

    def test_clear_switches_main_session(self):
        self.append(self.start_main("s1"), self.ev("UserPromptSubmit", "s1", prompt="x"))
        self.append(self.ev("SessionStart", "s2", has_transcript=True, cwd=self.cwd, source="clear"))
        snap = self.read()
        self.assertEqual((snap["state"], snap["main_session"]), ("idle", "s2"))

    def test_other_instance_lines_ignored(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", inst="ffffffffffff", prompt="old"))
        self.assertEqual(self.read()["state"], "idle")

    def test_cwd_symlink_matches(self):
        self.cwd = "/tmp"
        self.append(self.ev("SessionStart", has_transcript=True, cwd="/private/tmp", source="startup"))
        self.assertEqual(self.read()["state"], "idle")

    def test_malformed_complete_line_skipped(self):
        self.append(self.start_main(), "{not json\n", self.ev("UserPromptSubmit", prompt="x"))
        self.assertEqual(self.read()["state"], "working")

    def test_many_unknown_sessions_bounded(self):
        self.append(*[self.ev("PreToolUse", f"s{i}", tool_name="Bash") for i in range(500)])
        snap = self.read()
        self.assertLessEqual(len(snap["pending"]), events.MAX_PENDING)


class IncrementalReadTest(EventsTestCase):
    def test_consumed_region_is_not_reread(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"))
        self.assertEqual(self.read()["state"], "working")
        size = os.path.getsize(self.path)
        with open(self.path, "r+b") as f:   # 把已读过的部分原地改成垃圾
            f.write(b"#" * (size - 1))
        self.append(self.ev("Stop", last_assistant_message="r"))
        snap = self.read()
        self.assertEqual((snap["state"], snap["reply"]), ("idle", "r"))

    def test_partial_line_not_consumed(self):
        self.append(self.start_main())
        line = self.ev("UserPromptSubmit", prompt="x")
        self.append(line[:10])
        snap = self.read()
        self.assertEqual(snap["state"], "idle")
        self.append(line[10:])
        self.assertEqual(self.read()["state"], "working")

    def test_instance_change_recomputes(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"))
        self.assertEqual(self.read()["state"], "working")
        with open(self.path, "w") as f:  # start 会为新实例清空事件文件
            f.write(self.ev("SessionStart", inst="aaaaaaaaaaaa", has_transcript=True, cwd=self.cwd))
        self.assertEqual(self.read("aaaaaaaaaaaa")["state"], "idle")

    def test_shorter_file_recomputes(self):
        self.append(self.start_main(), self.ev("UserPromptSubmit", prompt="x"), self.ev("Stop"))
        self.read()
        with open(self.path, "w") as f:
            f.write(self.start_main())
        self.assertEqual(self.read()["state"], "idle")

    def test_concurrent_readers_agree(self):
        lines = [self.start_main()]
        for i in range(3000):
            lines.append(self.ev("UserPromptSubmit", prompt=f"p{i}"))
            lines.append(self.ev("Stop", last_assistant_message=f"r{i}"))
        self.append(*lines)
        code = ("import json,sys; sys.path.insert(0, %r); from corral import events; "
                "s = events.read(%r, %r, %r); print(json.dumps([s['state'], s['reply']]))"
                % (support.SRC, self.dir, INST, self.cwd))
        procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True) for _ in range(4)]
        results = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
        self.assertEqual(results, [["idle", "r2999"]] * 4)
        self.assertEqual([self.read()["state"], self.read()["reply"]], ["idle", "r2999"])

    def test_cursor_file_private(self):
        old = os.umask(0)
        try:
            self.append(self.start_main())
            self.read()
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "cursor")).st_mode), 0o600)


class FormatVersionTest(EventsTestCase):
    def test_unknown_format_is_incompatible(self):
        self.append(json.dumps({"v": 99, "t": 1.0, "ev": "Stop", "inst": INST}) + "\n")
        with self.assertRaises(errors.CorralError) as cm:
            self.read()
        self.assertEqual(cm.exception.exit_code, errors.EXIT_INCOMPATIBLE)

    def test_every_supported_format_has_readable_fixture(self):
        for version in events.EVENT_FORMATS:
            with self.subTest(version=version):
                fixture = os.path.join(support.ROOT, "tests", "fixtures", f"events_v{version}.jsonl")
                with open(fixture, encoding="utf-8") as f:
                    body = f.read().replace("__CWD__", self.cwd)
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(body)
                snap = self.read("f1f1f1f1f1f1")
                self.assertEqual((snap["state"], snap["reply"]), ("idle", "fixture reply"))


if __name__ == "__main__":
    unittest.main()
