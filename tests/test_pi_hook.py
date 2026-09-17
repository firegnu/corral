"""pi 的钩子扩展 hook_pi.ts：把 pi 事件翻译成 corral 事件，格式和 hook.py 一致。

扩展是 TypeScript，用本机的 bun 或能直接跑 .ts 的 node（23.6 起）执行；两者都没有时跳过。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import support

EXT = os.path.join(support.SRC, "corral", "hook_pi.ts")
HOOK_PY = os.path.join(support.SRC, "corral", "hook.py")

HARNESS = r"""
import { pathToFileURL } from "node:url";
const [ext, stepsJson, cwd] = process.argv.slice(-3);
const mod = await import(pathToFileURL(ext).href);
const handlers = {};
const pi = { on: (name, fn) => { (handlers[name] ||= []).push(fn); } };
await mod.default(pi);
for (const [name, event, idle] of JSON.parse(stepsJson)) {
  const ctx = { cwd, hasUI: true, isIdle: () => idle,
                sessionManager: { getSessionId: () => "s1", getSessionFile: () => "/tmp/s1.jsonl" } };
  for (const fn of handlers[name] || []) await fn(event, ctx);
}
"""


def runtime():
    if shutil.which("bun"):
        return ["bun", "run"]
    node = shutil.which("node")
    if node:
        out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
        major, minor = (int(x) for x in out.split(".")[:2])
        if (major, minor) >= (23, 6):
            return [node]
    return None


@unittest.skipIf(runtime() is None, "needs bun or node >= 23.6 to run the TypeScript extension")
class PiHookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.events = os.path.join(self.dir, "events")
        self.harness = os.path.join(self.dir, "harness.mjs")
        with open(self.harness, "w") as f:
            f.write(HARNESS)

    def run_steps(self, steps, env_events=True, events_path=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CORRAL_")}
        if env_events:
            env["CORRAL_EVENTS"] = events_path or self.events
            env["CORRAL_INSTANCE"] = "0123456789ab"
        proc = subprocess.run([*runtime(), self.harness, EXT, json.dumps(steps), self.dir],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        return proc

    def records(self):
        with open(self.events) as f:
            return [json.loads(line) for line in f]

    def test_maps_pi_events(self):
        assistant = lambda text, stop: {"message": {"role": "assistant", "stopReason": stop,  # noqa: E731
                                                    "content": [{"type": "thinking", "thinking": "hm"},
                                                                {"type": "text", "text": text}]}}
        self.run_steps([
            ["session_start", {"reason": "startup"}, True],
            ["input", {"text": "hello\nworld", "source": "interactive"}, True],
            ["before_agent_start", {"prompt": "hello\nworld (expanded)"}, False],
            ["tool_execution_start", {"toolName": "bash", "toolCallId": "t1"}, False],
            ["ui_prompt_start", {"kind": "confirm"}, False],
            ["ui_prompt_end", {}, False],
            ["tool_execution_end", {"toolName": "bash", "toolCallId": "t1"}, False],
            ["message_end", {"message": {"role": "user", "content": "ignored"}}, False],
            ["message_end", assistant("step one", "toolUse"), False],
            ["message_end", assistant("final answer", "stop"), False],
            ["agent_end", {"messages": []}, False],
            ["agent_settled", {}, True],
            ["ui_prompt_start", {"kind": "select"}, True],
            ["ui_prompt_end", {}, True],
            ["session_shutdown", {"reason": "new"}, True],
            ["session_shutdown", {"reason": "quit"}, True],
        ])
        recs = self.records()
        self.assertEqual([r["ev"] for r in recs], [
            "SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "PostToolUse", "PostToolUse",
            "Stop", "Notification", "Stop", "SessionEnd"])
        for r in recs:
            self.assertEqual((r["v"], r["inst"], r["session_id"], r["has_transcript"], r["cwd"]),
                             (1, "0123456789ab", "s1", True, self.dir))
            self.assertIsInstance(r["t"], float)
        self.assertEqual(recs[0]["source"], "startup")
        self.assertEqual(recs[1]["prompt"], "hello\nworld")  # 用展开前的原文，和 send 送出的对得上
        self.assertEqual((recs[2]["tool_name"], recs[3]["notification_type"]), ("bash", "permission_prompt"))
        self.assertEqual(recs[6]["last_assistant_message"], "final answer")
        self.assertNotIn("last_assistant_message", recs[8])  # 空闲时关掉弹框：回到 idle，不带回复

    def test_prompt_falls_back_when_no_input_event(self):
        self.run_steps([["session_start", {"reason": "startup"}, True],
                        ["before_agent_start", {"prompt": "from extension"}, False]])
        self.assertEqual(self.records()[1]["prompt"], "from extension")

    def test_silent_without_corral_environment(self):
        self.run_steps([["session_start", {"reason": "startup"}, True]], env_events=False)
        self.assertFalse(os.path.exists(self.events))

    def test_write_errors_are_swallowed(self):
        self.run_steps([["session_start", {"reason": "startup"}, True],
                        ["agent_settled", {}, True]], events_path=os.path.join(self.dir, "missing", "events"))

    def test_record_shape_matches_hook_py(self):
        self.run_steps([["session_start", {"reason": "startup"}, True],
                        ["message_end", {"message": {"role": "assistant", "content": [
                            {"type": "text", "text": "done"}]}}, False],
                        ["agent_settled", {}, True]])
        ts = {r["ev"]: r for r in self.records()}
        py_events = os.path.join(self.dir, "py-events")
        env = dict(os.environ, CORRAL_EVENTS=py_events, CORRAL_INSTANCE="0123456789ab")
        for ev, payload in (("SessionStart", {"session_id": "s1", "cwd": self.dir, "source": "startup",
                                              "transcript_path": "/tmp/s1.jsonl"}),
                            ("Stop", {"session_id": "s1", "cwd": self.dir, "transcript_path": "/tmp/s1.jsonl",
                                      "last_assistant_message": "done"})):
            subprocess.run(["/usr/bin/python3", "-I", "-S", HOOK_PY, ev], input=json.dumps(payload).encode(),
                           env=env, check=True, timeout=10)
        with open(py_events) as f:
            py = {r["ev"]: r for r in (json.loads(line) for line in f)}
        for ev in ("SessionStart", "Stop"):
            with self.subTest(ev=ev):
                self.assertEqual(set(ts[ev]), set(py[ev]))
                for key in py[ev]:
                    self.assertEqual(type(ts[ev][key]), type(py[ev][key]), key)
                    if key != "t":
                        self.assertEqual(ts[ev][key], py[ev][key], key)


if __name__ == "__main__":
    unittest.main()
