"""omp 的钩子扩展 hook_omp.ts：事件翻译、回合结束判断（去抖、willContinue、可重试出错）、只认主会话，格式和 hook.py 一致。

扩展是 TypeScript，用 bun 或能直接跑 .ts 的 node（23.6 起）执行；两者都没有时跳过。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import support
from tests.test_pi_hook import runtime

EXT = os.path.join(support.SRC, "corral", "hook_omp.ts")
HOOK_PY = os.path.join(support.SRC, "corral", "hook.py")

HARNESS = r"""
import { pathToFileURL } from "node:url";
const [ext, stepsJson, cwd] = process.argv.slice(-3);
const mod = await import(pathToFileURL(ext).href);
const handlers = {};
const pi = { on: (name, fn) => { (handlers[name] ||= []).push(fn); } };
await mod.default(pi);
for (const step of JSON.parse(stepsJson)) {
  if (step[0] === "sleep") { await new Promise((r) => setTimeout(r, step[1])); continue; }
  const [name, event, idle, hasUI] = step;
  const ctx = { cwd, hasUI: hasUI !== false, isIdle: () => idle,
                sessionManager: { getSessionId: () => "s1", getSessionFile: () => "/tmp/s1.jsonl" } };
  for (const fn of handlers[name] || []) await fn(event, ctx);
}
"""


def assistant(text, stop="stop", error=None):
    msg = {"role": "assistant", "stopReason": stop, "content": [{"type": "text", "text": text}]}
    if error:
        msg["errorMessage"] = error
    return msg


@unittest.skipIf(runtime() is None, "needs bun or node >= 23.6 to run the TypeScript extension")
class OmpHookTest(unittest.TestCase):
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

    def records(self):
        if not os.path.exists(self.events):
            return []
        with open(self.events) as f:
            return [json.loads(line) for line in f]

    def evs(self):
        return [r["ev"] for r in self.records()]

    def test_maps_omp_events(self):
        self.run_steps([
            ["session_start", {}, True],
            ["input", {"text": "hello\nworld"}, True],
            ["before_agent_start", {"prompt": "hello\nworld (joined)"}, False],
            ["agent_start", {}, False],
            ["tool_execution_start", {"toolName": "bash"}, False],
            ["tool_approval_requested", {"toolName": "bash"}, False],
            ["tool_approval_resolved", {"toolName": "bash"}, False],
            ["tool_execution_end", {"toolName": "bash"}, False],
            ["tool_execution_start", {"toolName": "ask", "args": {}}, False],
            ["tool_execution_end", {"toolName": "ask"}, False],
            ["message_end", {"message": assistant("step", "toolUse")}, False],
            ["message_end", {"message": assistant("final answer")}, False],
            ["agent_end", {"willContinue": False, "messages": [assistant("final answer")]}, False],
            ["sleep", 600],
            ["session_switch", {"reason": "new"}, True],
            ["session_shutdown", {}, True],
        ])
        recs = self.records()
        self.assertEqual([r["ev"] for r in recs], [
            "SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "PostToolUse", "PostToolUse",
            "Notification", "PostToolUse", "Stop", "SessionStart", "SessionEnd"])
        for r in recs:
            self.assertEqual((r["v"], r["inst"], r["session_id"], r["has_transcript"], r["cwd"]),
                             (1, "0123456789ab", "s1", True, self.dir))
            self.assertIsInstance(r["t"], float)
        self.assertEqual(recs[0]["source"], "startup")
        self.assertEqual(recs[1]["prompt"], "hello\nworld")
        self.assertEqual((recs[2]["tool_name"], recs[3]["notification_type"]), ("bash", "permission_prompt"))
        self.assertEqual(recs[6]["notification_type"], "permission_prompt")  # ask 工具：等人回答
        self.assertEqual(recs[8]["last_assistant_message"], "final answer")
        self.assertEqual(recs[9]["source"], "new")

    def test_stop_waits_for_debounce_and_skips_will_continue(self):
        self.run_steps([
            ["session_start", {}, True],
            ["agent_start", {}, False],
            ["agent_end", {"willContinue": True, "messages": [assistant("more to do")]}, False],
            ["sleep", 600],
        ])
        self.assertEqual(self.evs(), ["SessionStart"])

    def test_new_agent_start_cancels_pending_stop(self):
        self.run_steps([
            ["session_start", {}, True],
            ["agent_start", {}, False],
            ["agent_end", {"messages": [assistant("first")]}, False],
            ["sleep", 80],
            ["agent_start", {}, False],
            ["sleep", 600],
            ["message_end", {"message": assistant("second")}, False],
            ["agent_end", {"messages": [assistant("second")]}, False],
            ["sleep", 600],
        ])
        recs = self.records()
        self.assertEqual([r["ev"] for r in recs], ["SessionStart", "Stop"])
        self.assertEqual(recs[1]["last_assistant_message"], "second")

    def test_retryable_error_holds_longer(self):
        self.run_steps([
            ["session_start", {}, True],
            ["agent_start", {}, False],
            ["agent_end", {"messages": [assistant("", "error", "429 Too Many Requests")]}, False],
            ["sleep", 900],
        ])
        self.assertEqual(self.evs(), ["SessionStart"])
        self.run_steps([["sleep", 10]])  # 新进程不带之前的计时器：单独验证等满宽限后会结束
        self.run_steps([
            ["session_start", {}, True],
            ["agent_start", {}, False],
            ["agent_end", {"messages": [assistant("", "error", "rate limit exceeded")]}, False],
            ["sleep", 3200],
        ])
        self.assertEqual(self.evs()[-1], "Stop")

    def test_non_retryable_error_ends_after_debounce(self):
        self.run_steps([
            ["session_start", {}, True],
            ["agent_start", {}, False],
            ["agent_end", {"messages": [assistant("", "error", "invalid api key")]}, False],
            ["sleep", 600],
        ])
        self.assertEqual(self.evs(), ["SessionStart", "Stop"])

    def test_ignores_sessions_without_ui(self):
        self.run_steps([
            ["session_start", {}, True, False],
            ["before_agent_start", {"prompt": "sub"}, False, False],
            ["tool_execution_start", {"toolName": "bash"}, False, False],
            ["agent_end", {"messages": [assistant("sub done")]}, False, False],
            ["sleep", 600],
        ])
        self.assertEqual(self.records(), [])

    def test_silent_without_corral_environment(self):
        self.run_steps([["session_start", {}, True]], env_events=False)
        self.assertFalse(os.path.exists(self.events))

    def test_write_errors_are_swallowed(self):
        self.run_steps([["session_start", {}, True], ["agent_end", {"messages": []}, False], ["sleep", 600]],
                       events_path=os.path.join(self.dir, "missing", "events"))

    def test_record_shape_matches_hook_py(self):
        self.run_steps([["session_start", {}, True],
                        ["message_end", {"message": assistant("done")}, False],
                        ["agent_end", {"messages": [assistant("done")]}, False],
                        ["sleep", 600]])
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
