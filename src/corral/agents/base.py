"""钩子命令：运行 agent 状态目录里的 hook.py 副本，用 /usr/bin/python3（DESIGN 5.3）。"""
import shlex

HOOK_PYTHON = "/usr/bin/python3"


# 不认识的 agent：先挂断，再终止（栏位最后总会补一个 SIGKILL）
DEFAULT_QUIT = ({"signal": "HUP", "wait": 3}, {"signal": "TERM", "wait": 3})


def hook_command(hook_path, event):
    return " ".join([HOOK_PYTHON, "-I", "-S", shlex.quote(hook_path), event])
