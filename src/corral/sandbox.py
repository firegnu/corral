"""识别 Codex 沙箱。沙箱里连不上栏位、起不了栏位，所有碰 agent 的命令都直接拒绝。"""
import os

from corral.errors import EXIT_SANDBOX, CorralError


def check():
    if "CODEX_SANDBOX" in os.environ:
        raise CorralError(EXIT_SANDBOX, "sandbox",
                          "running inside a Codex sandbox: agents started here would be sandboxed too, "
                          "and running agents cannot be reached; start the caller without the sandbox "
                          "(e.g. codex --yolo)")
