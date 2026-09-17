"""agent 适配：认识 Claude Code、Codex、pi 和 omp，按启动参数给它们加钩子、带首句。

钩子只随 corral 启动的那个 agent 生效，不写任何全局配置。Claude Code、Codex 的钩子命令用 /usr/bin/python3 运行
agent 状态目录里的 hook.py 副本；pi、omp 用 --extension 加载状态目录里的
hook_pi.ts / hook_omp.ts 副本。都不引用 corral 仓库和
start 时的 Python（DESIGN 5.3）。
"""
import os

from corral.agents.base import DEFAULT_QUIT, HOOK_PYTHON, hook_command
from corral.agents.claude import CLAUDE_EVENTS, Claude
from corral.agents.codex import CODEX_EVENTS, Codex
from corral.agents.omp import Omp
from corral.agents.pi import Pi

ADAPTERS = {a.kind: a for a in (Claude(), Codex(), Pi(), Omp())}

__all__ = ["ADAPTERS", "CLAUDE_EVENTS", "CODEX_EVENTS", "HOOK_PYTHON", "for_command", "hook_command",
           "quit_steps"]


def quit_steps(kind):
    adapter = ADAPTERS.get(kind)
    return list(adapter.quit_steps if adapter else DEFAULT_QUIT)


def for_command(argv):
    """按 agent 命令的程序名找适配器；不认识返回 None。"""
    return ADAPTERS.get(os.path.basename(argv[0]))
