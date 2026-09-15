"""Claude Code：用 --settings 按启动参数加钩子（和用户全局设置里的钩子同时生效）；首句是最后一个参数。"""
import json

from corral.agents.base import hook_command

CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                 "Notification", "Stop", "StopFailure", "SessionEnd")
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PermissionRequest")


class Claude:
    kind = "claude"
    # 实测收到 SIGHUP 约 1 秒正常退出
    quit_steps = ({"signal": "HUP", "wait": 5}, {"signal": "TERM", "wait": 3})

    def build(self, argv, hook_path, prompt=None):
        hooks = {}
        for ev in CLAUDE_EVENTS:
            entry = {"hooks": [{"type": "command", "command": hook_command(hook_path, ev), "timeout": 10}]}
            if ev in TOOL_EVENTS:
                entry["matcher"] = "*"
            hooks[ev] = [entry]
        out = [argv[0], "--settings", json.dumps({"hooks": hooks}, ensure_ascii=False), *argv[1:]]
        if prompt is not None:
            # -- 结束选项解析：--allowedTools 这类多值选项会把追加在后面的首句当成自己的值吞掉（M8 实测）
            out += ["--", prompt]
        return out
