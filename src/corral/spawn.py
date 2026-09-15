"""corral start：加锁、建目录、两次 fork 起栏位、等栏位回报。"""
import fcntl
import itertools
import json
import os
import select
import subprocess
import time
import uuid

from corral import __version__, agents, env, events, paths, pen
from corral.errors import EXIT_ERROR, EXIT_EXISTS, CorralError

READY_TIMEOUT = 15.0
STALE_FILES = ("sock", "meta.json", "exit.json", "cursor", "pen.log", "hook.py")
HOOK_PYTHON = agents.HOOK_PYTHON
HOOK_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")


def open_private(path, flags):
    fd = os.open(path, flags | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def ensure_private_dirs(name):
    """逐层建名字目录。只改自己新建的目录的权限（状态目录可能是调用方指定的现有目录）。"""
    d = paths.state_home()
    levels = [d] + [os.path.join(d, *name.split("/")[:i]) for i in range(1, name.count("/") + 2)]
    for level in levels:
        try:
            os.mkdir(level, 0o700)
        except FileExistsError:
            continue
        os.chmod(level, 0o700)


def try_lock(name):
    """拿名字的文件锁。拿到后确认锁住的还是目录里那个文件（ls 清残留时可能刚把它删掉）。"""
    lock_path = os.path.join(paths.pen_dir(name), "lock")
    while True:
        fd = open_private(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        try:
            same = os.stat(lock_path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if same:
            return fd
        os.close(fd)


def acquire(name, unique):
    candidates = (f"{name}-{n}" for n in itertools.count(1)) if unique else iter([name])
    for cand in candidates:
        paths.sock_path(cand)  # 先检查路径长度，再建目录
        ensure_private_dirs(cand)
        fd = try_lock(cand)
        if fd is not None:
            return cand, fd
        if not unique:
            raise CorralError(EXIT_EXISTS, "exists", f"{name} is already running", name=name)
    raise AssertionError("unreachable")


def clear_stale(d):
    for f in STALE_FILES:
        try:
            os.unlink(os.path.join(d, f))
        except FileNotFoundError:
            pass


def check_hook_python():
    """钩子由 /usr/bin/python3 运行。没装开发者工具的 Mac 上它是会弹安装窗口的占位程序，先试跑一次。"""
    try:
        proc = subprocess.run([HOOK_PYTHON, "-I", "-S", "-c", "import json, os, sys, time"],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        raise CorralError(EXIT_ERROR, "hook_python_unavailable",
                          f"{HOOK_PYTHON} is not usable; agent hooks need it (on macOS install the "
                          f"Command Line Tools: xcode-select --install)")


def prepare_files(d, adapter):
    """事件文件清空重建；认识的 agent 复制一份钩子脚本。都是 0600。"""
    fd = open_private(os.path.join(d, "events"), os.O_WRONLY | os.O_TRUNC)
    os.close(fd)
    if adapter is None:
        return None
    hook_path = os.path.join(d, "hook.py")
    with open(HOOK_SOURCE, "rb") as src:
        body = src.read()
    fd = open_private(hook_path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    return hook_path


def parse_env_pairs(pairs):
    extra = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise CorralError(EXIT_ERROR, "usage", f"--env expects KEY=VALUE, got {pair!r}")
        extra[key] = value
    return extra


def start(name, cwd, argv, unique=False, env_pairs=(), prompt=None):
    cwd = os.path.abspath(cwd or os.getcwd())
    if not os.path.isdir(cwd):
        raise CorralError(EXIT_ERROR, "bad_cwd", f"no such directory: {cwd}", cwd=cwd)
    extra = parse_env_pairs(env_pairs)
    paths.validate_name(name)
    adapter = agents.for_command(argv)
    if prompt is not None and adapter is None:
        raise CorralError(EXIT_ERROR, "usage", f"--prompt is only supported for {sorted(agents.ADAPTERS)}")
    if adapter is not None:
        check_hook_python()
    name, lock_fd = acquire(name, unique)
    d = paths.pen_dir(name)
    clear_stale(d)
    hook_path = prepare_files(d, adapter)
    instance = uuid.uuid4().hex[:12]
    agent_env, warnings = env.build(name, instance, os.path.join(d, "events"), paths.state_home(), extra)
    agent_argv = adapter.build(list(argv), hook_path, prompt) if adapter else list(argv)
    cfg = {"name": name, "instance": instance, "dir": d, "cwd": cwd, "argv": agent_argv,
           "kind": adapter.kind if adapter else os.path.basename(argv[0]), "env": agent_env,
           "version": __version__, "prompt_digest": events.digest(prompt) if prompt is not None else None}

    ready_r, ready_w = os.pipe()
    child = os.fork()
    if child == 0:
        _become_pen(cfg, lock_fd, ready_r, ready_w)
    os.close(ready_w)
    os.close(lock_fd)
    os.waitpid(child, 0)
    msg = _read_ready(ready_r)
    os.close(ready_r)
    if msg is None:
        raise CorralError(EXIT_ERROR, "pen_failed", f"pen for {name} did not start", name=name)
    if not msg.get("ok"):
        raise CorralError(EXIT_ERROR, msg.get("error", "pen_failed"), msg.get("message", ""), name=name)
    result = {"ok": True, "name": name, "instance": instance, "kind": cfg["kind"]}
    if warnings:
        result["warnings"] = warnings
    return result


def _become_pen(cfg, lock_fd, ready_r, ready_w):
    """在子进程里：开新会话、再 fork 一次脱离，孙进程成为栏位。不返回。"""
    try:
        os.setsid()
        if os.fork() != 0:
            os._exit(0)
        os.close(ready_r)
        log = open_private(os.path.join(cfg["dir"], "pen.log"), os.O_WRONLY | os.O_TRUNC)
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(log, 1)
        os.dup2(log, 2)
        keep = {0, 1, 2, lock_fd, ready_w}
        for fd in range(3, 1024):
            if fd not in keep:
                try:
                    os.close(fd)
                except OSError:
                    pass
        code = pen.run(cfg, ready_w)
    except BaseException:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        code = 1
    os._exit(code)


def _read_ready(fd):
    buf = b""
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], max(0.0, deadline - time.time()))
        if not r:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    try:
        return json.loads(buf.split(b"\n", 1)[0]) if buf.strip() else None
    except ValueError:
        return None
