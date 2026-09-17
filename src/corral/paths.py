"""状态目录、名字校验、socket 路径。"""
import os
import re

from corral.errors import EXIT_ERROR, CorralError

STATE_HOME_ENV = "CORRAL_HOME"
SOCK_PATH_MAX = 104 if os.uname().sysname == "Darwin" else 108

SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# 栏位目录里的内部文件；名字的段不能和它们重名
INTERNAL_FILES = frozenset({"lock", "meta.json", "sock", "hook.py", "hook_pi.ts", "events", "cursor", "exit.json",
                            "pen.log"})


def state_home():
    return os.environ.get(STATE_HOME_ENV) or os.path.join(os.path.expanduser("~"), ".corral")


def validate_name(name):
    segments = name.split("/")
    for seg in segments:
        if not SEGMENT_RE.match(seg) or seg in INTERNAL_FILES:
            raise CorralError(EXIT_ERROR, "bad_name",
                              f"bad name {name!r}: segments are letters, digits, '.', '_', '-', "
                              f"start with a letter or digit, and are not internal file names",
                              name=name)


def pen_dir(name):
    validate_name(name)
    return os.path.join(state_home(), name)


def sock_path(name):
    path = os.path.join(pen_dir(name), "sock")
    if len(os.fsencode(path)) >= SOCK_PATH_MAX:
        raise CorralError(EXIT_ERROR, "path_too_long",
                          f"socket path {path!r} is too long (limit {SOCK_PATH_MAX} bytes); "
                          f"use a shorter name or a shorter {STATE_HOME_ENV}",
                          name=name)
    return path
