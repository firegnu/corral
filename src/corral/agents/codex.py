"""Codex：用 -c 按启动参数加钩子，必须带 --dangerously-bypass-hook-trust。

不带的话启动会弹「钩子需要审核」，点「全部信任」会把钩子哈希写进用户的 config.toml（违反「不动全局配置」）。
Codex 的会话开始事件要到第一次提交输入才触发，所以新开的 Codex 第一句话只能走启动参数。
"""
import base64
import json

from corral.agents.base import hook_command

CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop",
                "Interrupt")
TIMEOUTS = {"Interrupt": 3}  # Codex 把 Interrupt 钩子的超时截到 3 秒，给多了会有提示
DEFAULT_TIMEOUT = 10


def toml_string(s):
    # JSON 字符串（ensure_ascii）也是合法的 TOML 基本字符串
    return json.dumps(s, ensure_ascii=True)


CTRL_C = base64.b64encode(b"\x03").decode()


class Codex:
    kind = "codex"
    hook_file = "hook.py"
    needs_hook_python = True
    # 实测不理 SIGHUP。连按两次 Ctrl-C 会显示「Shutting down...」并正常退出（退出码 0），但收尾很久且随会话
    # 内容增长（M8 刚起的会话 7.6 秒；corral-lab 实测跑过三轮后 27.7 秒，两次 Ctrl-C 的间隔不是原因），收尾期间
    # 没有任何输出可供判断，只能等：取栏位允许的最长一步 60 秒（观测值的两倍）再终止，免得打断它收尾
    quit_steps = ({"keys": CTRL_C, "wait": 0.3}, {"keys": CTRL_C, "wait": 60}, {"signal": "TERM", "wait": 3})

    def build(self, argv, hook_path, prompt=None):
        extra = []
        for ev in CODEX_EVENTS:
            timeout = TIMEOUTS.get(ev, DEFAULT_TIMEOUT)
            extra += ["-c", f"hooks.{ev}=[{{hooks=[{{type=\"command\",command={toml_string(hook_command(hook_path, ev))},"
                            f"timeout={timeout}}}]}}]"]
        out = [argv[0], *extra, "--dangerously-bypass-hook-trust", *argv[1:]]
        if prompt is not None:
            out += ["--", prompt]  # 同 Claude Code：防止被多值选项吞掉
        return out
