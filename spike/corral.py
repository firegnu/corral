#!/usr/bin/env python3
"""corral 试验版：栏位 + 命令，全在一个文件里。粗糙，不是正式版。

用法见 cmd_* 函数；状态目录默认 /tmp/crl，可用 CORRAL_HOME 改。
"""
import base64
import errno
import fcntl
import json
import os
import pty
import re
import selectors
import shlex
import signal
import socket
import struct
import sys
import termios
import time
import traceback
import tty
import uuid

HOME_DIR = os.environ.get("CORRAL_HOME", "/tmp/crl")
HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "hook.py")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
DETACH_KEY = b"\x1d"  # Ctrl-]
# agent 打开 kitty 键盘协议后，终端把 Ctrl-] 编成 CSI u：ESC [ 93 ; 5 u（可带替代键码、事件类型）
DETACH_CSI_U = re.compile(rb"\x1b\[93(?::[\d:]*)?;5(?::\d+)?u")
RING_MAX = 512 * 1024
DEFAULT_ROWS, DEFAULT_COLS = 40, 120

EXIT_ERR, EXIT_NOTFOUND, EXIT_NOTDELIVERED, EXIT_TIMEOUT, EXIT_EXISTS, EXIT_REFUSED = 1, 2, 3, 4, 5, 6

CLAUDE_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                 "Notification", "Stop", "StopFailure", "SessionEnd"]
CODEX_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                "Stop", "Interrupt"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}

ENV_KEEP = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "SSH_AUTH_SOCK",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


def die(code, msg, **kw):
    print(json.dumps({"ok": False, "error": msg, **kw}, ensure_ascii=False))
    sys.exit(code)


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def pen_dir(name):
    if not NAME_RE.match(name) or any(p in (".", "..") for p in name.split("/")):
        die(EXIT_ERR, f"bad name: {name}")
    d = os.path.join(HOME_DIR, name)
    if len(os.path.join(d, "sock").encode()) >= 104:
        die(EXIT_ERR, "socket path too long (macOS limit 104 bytes)")
    return d


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 栏位

def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


MODE_RE = re.compile(rb"\x1b\[\?([\d;]+)([hl])")
KITTY_RE = re.compile(rb"\x1b\[([<>=])(\d*)(?:;(\d*))?u")
TITLE_RE = re.compile(rb"\x1b\][02];([^\x07\x1b]*)(?:\x07|\x1b\\)")
TRACKED_MODES = {"25", "47", "1047", "1049", "1000", "1002", "1003", "1004", "1006", "2004", "2026"}


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.inbuf = b""
        self.out = bytearray()
        self.mode = "handshake"   # handshake / attached / closing
        self.rows = DEFAULT_ROWS
        self.cols = DEFAULT_COLS


def pen_main(name, instance, cwd, argv, env, ready_w):
    d = pen_dir(name)
    sockpath = os.path.join(d, "sock")
    try:
        os.unlink(sockpath)
    except FileNotFoundError:
        pass

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except Exception as e:  # noqa: BLE001
            os.write(2, f"corral: exec failed: {e}\n".encode())
        os._exit(127)

    set_size(master, DEFAULT_ROWS, DEFAULT_COLS)
    os.set_blocking(master, False)
    old = os.umask(0o077)
    lst = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lst.bind(sockpath)
    os.umask(old)
    os.chmod(sockpath, 0o600)
    lst.listen(16)
    lst.setblocking(False)

    started = time.time()
    write_json(os.path.join(d, "meta.json"), {
        "name": name, "instance": instance, "pen_pid": os.getpid(), "agent_pid": pid, "cwd": cwd,
        "argv": argv, "kind": os.path.basename(argv[0]), "started": started,
    })
    os.write(ready_w, f"ok {pid}\n".encode())
    os.close(ready_w)

    sel = selectors.DefaultSelector()
    sel.register(master, selectors.EVENT_READ, "pty")
    sel.register(lst, selectors.EVENT_READ, "listen")
    clients = {}
    writer = None
    ring = bytearray()
    carry = b""
    last_output = None
    title = ""
    modes = {}          # DEC 私有模式 -> "h"/"l"
    kitty = []          # kitty 键盘协议标志栈
    inq = []            # [bytes, delay_after, reply_client]
    next_write = 0.0
    timers = []         # [when, fn]
    size = [DEFAULT_ROWS, DEFAULT_COLS]
    exit_code = None
    exit_at = 0.0
    pty_open = True

    def resize(rows, cols, force=False):
        size[:] = [rows, cols]
        if force:
            set_size(master, rows, max(cols - 1, 10))
            timers.append([time.time() + 0.15, lambda: set_size(master, rows, cols)])
        else:
            set_size(master, rows, cols)

    def send_to(c, data):
        c.out += data
        try:
            sel.modify(c.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, c)
        except (KeyError, ValueError):
            pass

    def reply(c, obj):
        send_to(c, (json.dumps(obj, ensure_ascii=False) + "\n").encode())
        c.mode = "closing"

    def drop(c):
        nonlocal writer
        try:
            sel.unregister(c.sock)
        except (KeyError, ValueError):
            pass
        c.sock.close()
        clients.pop(c.sock, None)
        if writer is c:
            writer = next((x for x in clients.values() if x.mode == "attached"), None)
            if writer:
                resize(writer.rows, writer.cols)

    def attach_prelude():
        seq = b""
        for m, v in modes.items():
            seq += b"\x1b[?" + m.encode() + v.encode()
        if kitty:
            seq += b"\x1b[>" + str(kitty[-1]).encode() + b"u"
        return seq

    def handle_request(c, req):
        nonlocal writer
        op = req.get("op")
        if op in ("write", "keys"):
            chunks = req["chunks"]
            for i, ch in enumerate(chunks):
                inq.append([base64.b64decode(ch["data"]), ch.get("delay", 0), c if i == len(chunks) - 1 else None])
            c.mode = "waiting"
        elif op == "status":
            reply(c, {"ok": True, "instance": instance, "agent_pid": pid, "started": started,
                      "last_output": last_output, "title": title, "size": size,
                      "attached": sum(1 for x in clients.values() if x.mode == "attached")})
        elif op == "read":
            reply(c, {"ok": True, "data": base64.b64encode(bytes(ring[-int(req.get("bytes", 16000)):])).decode()})
        elif op == "stop":
            try:
                os.killpg(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            for delay, sig in ((5, signal.SIGTERM), (10, signal.SIGKILL)):
                timers.append([time.time() + delay, lambda s=sig: _kill(pid, s)])
            reply(c, {"ok": True})
        elif op == "attach":
            c.mode = "attached"
            c.rows, c.cols = int(req.get("rows", DEFAULT_ROWS)), int(req.get("cols", DEFAULT_COLS))
            readonly = writer is not None
            send_to(c, (json.dumps({"ok": True, "instance": instance, "readonly": readonly,
                                    "modes": modes, "kitty": bool(kitty)}) + "\n").encode())
            send_to(c, attach_prelude())
            if not readonly:
                writer = c
                resize(c.rows, c.cols, force=True)
        else:
            reply(c, {"ok": False, "error": f"bad op {op}"})

    def handle_frames(c):
        while len(c.inbuf) >= 5:
            typ = c.inbuf[:1]
            n = struct.unpack(">I", c.inbuf[1:5])[0]
            if len(c.inbuf) < 5 + n:
                return
            payload, c.inbuf = c.inbuf[5:5 + n], c.inbuf[5 + n:]
            if c is not writer:
                continue
            if typ == b"i":
                inq.append([payload, 0, None])
            elif typ == b"r":
                c.rows, c.cols = struct.unpack(">HH", payload)
                resize(c.rows, c.cols)

    def on_output(data):
        nonlocal carry, last_output, title
        last_output = time.time()
        ring.extend(data)
        if len(ring) > RING_MAX:
            del ring[:len(ring) - RING_MAX]
        scan = carry + data
        for m in TITLE_RE.finditer(scan):
            title = m.group(1).decode("utf-8", "replace")
        for m in MODE_RE.finditer(scan):
            for num in m.group(1).decode().split(";"):
                if num in TRACKED_MODES:
                    modes[num] = m.group(2).decode()
        for m in KITTY_RE.finditer(scan):
            kind, a = m.group(1), m.group(2)
            if kind == b">":
                kitty.append(int(a or 0))
            elif kind == b"<":
                del kitty[max(0, len(kitty) - int(a or 1)):]
            elif kind == b"=" and kitty:
                kitty[-1] = int(a or 0)
        carry = scan[-256:]
        for c in list(clients.values()):
            if c.mode == "attached":
                send_to(c, data)
                if len(c.out) > 8 * 1024 * 1024:
                    drop(c)

    while True:
        now = time.time()
        for t in [t for t in timers if t[0] <= now]:
            timers.remove(t)
            try:
                t[1]()
            except OSError:
                pass
        if pty_open:
            want = selectors.EVENT_READ
            if inq and now >= next_write:
                want |= selectors.EVENT_WRITE
            sel.modify(master, want, "pty")
        deadlines = [t[0] for t in timers] + ([next_write] if inq else [])
        timeout = max(0.0, min(deadlines) - now) if deadlines else 1.0
        for key, ev in sel.select(min(timeout, 1.0)):
            if key.data == "pty":
                if ev & selectors.EVENT_READ:
                    try:
                        data = os.read(master, 65536)
                    except OSError as e:
                        data = b"" if e.errno == errno.EIO else None
                    if data:
                        on_output(data)
                    elif data == b"":
                        sel.unregister(master)
                        pty_open = False
                        continue
                if ev & selectors.EVENT_WRITE and inq:
                    item = inq[0]
                    try:
                        n = os.write(master, item[0])
                    except BlockingIOError:
                        n = 0
                    item[0] = item[0][n:]
                    if not item[0]:
                        inq.pop(0)
                        next_write = time.time() + item[1]
                        if item[2] is not None:
                            reply(item[2], {"ok": True})
            elif key.data == "listen":
                try:
                    s, _ = lst.accept()
                except BlockingIOError:
                    continue
                s.setblocking(False)
                c = Client(s)
                clients[s] = c
                sel.register(s, selectors.EVENT_READ, c)
            else:
                c = key.data
                if ev & selectors.EVENT_READ:
                    try:
                        data = c.sock.recv(65536)
                    except BlockingIOError:
                        data = None
                    except ConnectionError:
                        data = b""
                    if data == b"":
                        drop(c)
                        continue
                    if data:
                        c.inbuf += data
                        if c.mode == "handshake" and b"\n" in c.inbuf:
                            line, c.inbuf = c.inbuf.split(b"\n", 1)
                            try:
                                handle_request(c, json.loads(line))
                            except (ValueError, KeyError) as e:
                                reply(c, {"ok": False, "error": str(e)})
                        if c.mode == "attached":
                            handle_frames(c)
                if ev & selectors.EVENT_WRITE and c.sock in clients:
                    try:
                        n = c.sock.send(c.out)
                    except BlockingIOError:
                        n = 0
                    except ConnectionError:
                        drop(c)
                        continue
                    del c.out[:n]
                    if not c.out:
                        if c.mode == "closing":
                            drop(c)
                        else:
                            sel.modify(c.sock, selectors.EVENT_READ, c)

        if exit_code is None:
            try:
                wpid, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                wpid, status = pid, 0
            if wpid == pid:
                exit_code = os.waitstatus_to_exitcode(status)
                exit_at = time.time() + 0.3
                timers.append([exit_at, lambda: None])
        if exit_code is not None and (not pty_open or time.time() >= exit_at):
            break

    write_json(os.path.join(d, "exit.json"), {"instance": instance, "code": exit_code, "t": time.time()})
    try:
        os.unlink(sockpath)
    except FileNotFoundError:
        pass
    for c in list(clients.values()):
        try:
            c.sock.setblocking(True)
            c.sock.settimeout(0.5)
            if c.out:
                c.sock.sendall(c.out)
        except OSError:
            pass
        c.sock.close()


def _kill(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------- 命令侧

def connect(name, timeout=5):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(os.path.join(pen_dir(name), "sock"))
    except OSError:
        s.close()
        return None
    return s


def request(name, req, timeout=30):
    s = connect(name)
    if s is None:
        return None
    s.settimeout(timeout)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf) if buf else None


def clean_env(name, instance, events):
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP or k.startswith("LC_")}
    env.update(TERM="xterm-256color", COLORTERM="truecolor",
               CORRAL_NAME=name, CORRAL_INSTANCE=instance, CORRAL_EVENTS=events, CORRAL_HOME=HOME_DIR)
    return env


def toml_str(s):
    return json.dumps(s)


def adapt(argv, codex_hooks):
    kind = os.path.basename(argv[0])
    hook = lambda ev: f"{shlex.quote(sys.executable)} {shlex.quote(HOOK)} {ev}"
    if kind == "claude":
        hooks = {}
        for ev in CLAUDE_EVENTS:
            entry = {"hooks": [{"type": "command", "command": hook(ev), "timeout": 10}]}
            if ev in TOOL_EVENTS:
                entry["matcher"] = "*"
            hooks[ev] = [entry]
        return [argv[0], "--settings", json.dumps({"hooks": hooks})] + argv[1:]
    if kind == "codex" and codex_hooks != "none":
        extra = []
        for ev in CODEX_EVENTS:
            extra += ["-c", f'hooks.{ev}=[{{hooks=[{{type="command",command={toml_str(hook(ev))},timeout=10}}]}}]']
        if codex_hooks == "cli+bypass":
            extra.append("--dangerously-bypass-hook-trust")
        return [argv[0]] + extra + argv[1:]
    return list(argv)


def try_lock(d):
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(d, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def cmd_start(args):
    if "--" not in args:
        die(EXIT_ERR, "usage: start <name> [--cwd DIR] [--unique] [--codex-hooks cli|cli+bypass|none] -- <cmd...>")
    i = args.index("--")
    opts, argv = args[:i], args[i + 1:]
    name, cwd, unique, codex_hooks = None, os.getcwd(), False, "cli+bypass"
    it = iter(opts)
    for a in it:
        if a == "--cwd":
            cwd = next(it)
        elif a == "--unique":
            unique = True
        elif a == "--codex-hooks":
            codex_hooks = next(it)
        else:
            name = a
    if not name or not argv:
        die(EXIT_ERR, "need name and command")
    if "CODEX_SANDBOX" in os.environ:
        die(EXIT_REFUSED, "当前在 Codex 沙箱里，拉起的 agent 会被一起关进去；请用 --yolo 启动调用方")
    cwd = os.path.abspath(cwd)
    if not os.path.isdir(cwd):
        die(EXIT_ERR, f"no such dir: {cwd}")

    if unique:
        n = 1
        while True:
            cand = f"{name}-{n}"
            lockfd = try_lock(pen_dir(cand))
            if lockfd is not None:
                name = cand
                break
            n += 1
    else:
        lockfd = try_lock(pen_dir(name))
        if lockfd is None:
            die(EXIT_EXISTS, f"{name} already running")
    d = pen_dir(name)
    instance = uuid.uuid4().hex[:12]
    events = os.path.join(d, "events")
    open(events, "w").close()
    os.chmod(events, 0o600)
    for f in ("exit.json", "meta.json"):
        try:
            os.unlink(os.path.join(d, f))
        except FileNotFoundError:
            pass
    agent_argv = adapt(argv, codex_hooks)
    env = clean_env(name, instance, events)

    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        if os.fork() != 0:
            os._exit(0)
        os.close(r)
        log = os.open(os.path.join(d, "pen.log"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(log, 1)
        os.dup2(log, 2)
        for fd in range(3, 1024):
            if fd not in (w, lockfd):
                try:
                    os.close(fd)
                except OSError:
                    pass
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            pen_main(name, instance, cwd, agent_argv, env, w)
        except BaseException:  # noqa: BLE001
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    os.close(w)
    os.close(lockfd)
    os.waitpid(pid, 0)
    msg = b""
    while True:
        chunk = os.read(r, 4096)
        if not chunk:
            break
        msg += chunk
    if not msg.startswith(b"ok"):
        die(EXIT_ERR, "pen failed to start", log=os.path.join(d, "pen.log"))
    out({"ok": True, "name": name, "instance": instance})


def load_events(name, instance):
    evs = []
    try:
        with open(os.path.join(pen_dir(name), "events"), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("inst") == instance:
                    evs.append(e)
    except FileNotFoundError:
        pass
    return evs


def main_session(evs, cwd):
    """主会话 = 最近一次「工作目录和栏位一致、并且有对话记录」的会话开始。
    agent 内部的子会话（如 Codex 的记忆整理）继承同一套钩子，靠 session_id 排除。"""
    sid = None
    for e in evs:
        d = e.get("d") or {}
        if e["ev"] == "SessionStart" and d.get("transcript_path") and cwd and \
                os.path.realpath(d.get("cwd") or "") == os.path.realpath(cwd):
            sid = d.get("session_id")
    return sid


def agent_events(name, instance):
    evs = load_events(name, instance)
    cwd = (read_json(os.path.join(pen_dir(name), "meta.json")) or {}).get("cwd")
    sid = main_session(evs, cwd)
    return [e for e in evs if (e.get("d") or {}).get("session_id") == sid]


def compute_state(evs):
    state, tool, turn_started, last = "starting", None, None, None
    for e in evs:
        ev, d = e["ev"], e.get("d") or {}
        last = ev
        if ev == "SessionStart":
            if d.get("source") != "compact":
                state = "idle"
        elif ev == "UserPromptSubmit":
            state, turn_started, tool = "working", e["t"], None
        elif ev in ("PreToolUse", "PostToolUse"):
            state = "working"
            tool = d.get("tool_name", tool)
        elif ev == "PermissionRequest":
            state = "blocked"
            tool = d.get("tool_name", tool)
        elif ev == "Notification":
            if d.get("notification_type") == "permission_prompt":
                state = "blocked"
        elif ev in ("Stop", "StopFailure", "Interrupt"):
            state = "idle"
        elif ev == "SessionEnd":
            state = "exiting"
    return {"state": state, "last_tool": tool, "turn_started": turn_started, "last_event": last}


def get_status(name):
    st = request(name, {"op": "status"})
    d = pen_dir(name)
    if st is None:
        ex = read_json(os.path.join(d, "exit.json"))
        return None, ex
    meta = read_json(os.path.join(d, "meta.json")) or {}
    res = {"ok": True, "name": name, "instance": st["instance"], "kind": meta.get("kind"),
           "title": st["title"], "last_output": st["last_output"], "attached": st["attached"]}
    if meta.get("kind") in ("claude", "codex"):
        res.update(compute_state(agent_events(name, st["instance"])))
    else:
        res["state"] = "unknown"
    res["idle_for"] = round(time.time() - st["last_output"], 1) if st["last_output"] else None
    return res, None


def cmd_status(args):
    res, ex = get_status(args[0])
    if res is None:
        die(EXIT_NOTFOUND, "not found", exited=ex)
    out(res)


def cmd_send(args):
    name, text = args[0], args[1]
    timeout = 15.0
    enter_delay = float(os.environ.get("CORRAL_ENTER_DELAY", "0.3"))
    res, _ = get_status(name)
    if res is None:
        die(EXIT_NOTFOUND, "not found")
    inst, kind = res["instance"], res["kind"]
    if kind in ("claude", "codex") and res["state"] != "idle":
        die(EXIT_NOTDELIVERED, f"not idle: {res['state']}", name=name, instance=inst, state=res["state"])
    body = text.encode()
    if "\n" in text:
        body = b"\x1b[200~" + body + b"\x1b[201~"
    t0 = time.time()
    request(name, {"op": "write", "chunks": [
        {"data": base64.b64encode(body).decode(), "delay": enter_delay},
        {"data": base64.b64encode(b"\r").decode()}]})
    if kind not in ("claude", "codex"):
        out({"ok": True, "name": name, "instance": inst, "confirmed": False})
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 按文字匹配：agent 自己注入的输入（如后台任务完成通知）也会触发输入事件
        if any(e["ev"] == "UserPromptSubmit" and e["t"] >= t0 and
               ((e.get("d") or {}).get("prompt") or "").strip() == text.strip()
               for e in agent_events(name, inst)):
            out({"ok": True, "name": name, "instance": inst, "confirmed": True,
                 "latency": round(time.time() - t0, 2)})
            return
        time.sleep(0.1)
    die(EXIT_NOTDELIVERED, "no UserPromptSubmit event", name=name, instance=inst)


KEYS = {"enter": b"\r", "esc": b"\x1b", "tab": b"\t", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
        "left": b"\x1b[D", "space": b" ", "backspace": b"\x7f", "ctrl-c": b"\x03", "ctrl-d": b"\x04"}


def cmd_keys(args):
    name = args[0]
    chunks = []
    for k in args[1:]:
        data = k[5:].encode() if k.startswith("text:") else KEYS.get(k)
        if data is None:
            die(EXIT_ERR, f"unknown key {k}")
        chunks.append({"data": base64.b64encode(data).decode(), "delay": 0.1})
    if request(name, {"op": "keys", "chunks": chunks}) is None:
        die(EXIT_NOTFOUND, "not found")
    out({"ok": True})


def cmd_wait(args):
    name = args[0]
    timeout = float(args[1]) if len(args) > 1 else 600.0
    deadline = time.time() + timeout
    while True:
        res, ex = get_status(name)
        if res is None:
            die(EXIT_NOTFOUND, "not found", exited=ex)
        if res["state"] in ("idle", "blocked", "unknown"):
            # 会话开始和输入事件可能相隔几十毫秒先后到，idle 要稳定一小段才算
            time.sleep(0.5)
            again, _ = get_status(name)
            if again is not None and again["state"] == res["state"] and again["last_event"] == res["last_event"]:
                out(again)
                return
            continue
        if time.time() > deadline:
            res["ok"] = False
            out(res)
            sys.exit(EXIT_TIMEOUT)
        time.sleep(0.25)


def claude_transcript_reply(path):
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
    target = None
    for e in reversed(entries):
        msg = e.get("message") or {}
        if e.get("type") == "assistant" and any(b.get("type") == "text" for b in msg.get("content") or []):
            target = msg.get("id")
            break
    if target is None:
        return None
    texts = []
    for e in entries:
        msg = e.get("message") or {}
        if e.get("type") == "assistant" and msg.get("id") == target:
            texts += [b["text"] for b in msg.get("content") or [] if b.get("type") == "text"]
    return "\n".join(texts)


def cmd_reply(args):
    name = args[0]
    st = request(name, {"op": "status"})
    if st is None:
        die(EXIT_NOTFOUND, "not found")
    stops = [e for e in agent_events(name, st["instance"]) if e["ev"] == "Stop"]
    if not stops:
        die(EXIT_ERR, "no finished turn yet")
    d = stops[-1].get("d") or {}
    text, source = d.get("last_assistant_message"), "hook"
    if text is None and d.get("transcript_path"):
        text, source = claude_transcript_reply(d["transcript_path"]), "transcript"
    out({"ok": True, "name": name, "instance": st["instance"], "text": text, "source": source})


ANSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[P^_][^\x1b]*\x1b\\|\x1b[@-Z\\-_=>]")


def cmd_read(args):
    res = request(args[0], {"op": "read", "bytes": int(args[1]) if len(args) > 1 else 16000})
    if res is None:
        die(EXIT_NOTFOUND, "not found")
    data = ANSI_RE.sub(b"", base64.b64decode(res["data"])).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    data = re.sub(rb"[\x00-\x08\x0b-\x1f\x7f]", b"", data)
    sys.stdout.write(data.decode("utf-8", "replace"))


def cmd_stop(args):
    if request(args[0], {"op": "stop"}) is None:
        die(EXIT_NOTFOUND, "not found")
    out({"ok": True})


def scan_names():
    names = []
    for root, dirs, files in os.walk(HOME_DIR):
        if "lock" in files:
            names.append(os.path.relpath(root, HOME_DIR))
    return sorted(names)


def cmd_ls(args):
    rows = []
    for name in scan_names():
        d = os.path.join(HOME_DIR, name)
        st = request(name, {"op": "status"})
        if st is None:
            fd = try_lock(d)
            if fd is not None:   # 没有栏位拿着锁：残留，清掉
                for f in ("sock", "meta.json", "events", "exit.json", "pen.log", "lock"):
                    try:
                        os.unlink(os.path.join(d, f))
                    except FileNotFoundError:
                        pass
                os.close(fd)
                try:
                    os.removedirs(d)
                except OSError:
                    pass
            continue
        meta = read_json(os.path.join(d, "meta.json")) or {}
        rows.append({"name": name, "instance": st["instance"], "kind": meta.get("kind"), "cwd": meta.get("cwd")})
    out({"ok": True, "agents": rows})


def cmd_where(args):
    name = args[0]
    st = request(name, {"op": "status"})
    if st is None:
        die(EXIT_NOTFOUND, "not found")
    meta = read_json(os.path.join(pen_dir(name), "meta.json")) or {}
    out({"ok": True, "name": name, "instance": st["instance"], "cwd": meta.get("cwd"), "kind": meta.get("kind"),
         "agent_pid": st["agent_pid"]})


def frame(typ, payload):
    return typ + struct.pack(">I", len(payload)) + payload


def attach_once(name):
    s = connect(name)
    if s is None:
        return "notfound"
    cols, rows = os.get_terminal_size()
    s.sendall((json.dumps({"op": "attach", "rows": rows, "cols": cols}) + "\n").encode())
    s.settimeout(None)
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            return "exited"
        buf += chunk
    line, rest = buf.split(b"\n", 1)
    hello = json.loads(line)
    reason = "exited"
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    old_handler = signal.signal(signal.SIGWINCH, lambda *_: os.write(wake_w, b"w"))
    old_tty = termios.tcgetattr(0)
    tty.setraw(0)
    try:
        os.write(1, f"\x1b]2;{name}\x07".encode())
        if rest:
            os.write(1, rest)
        sel = selectors.DefaultSelector()
        sel.register(0, selectors.EVENT_READ, "in")
        sel.register(s, selectors.EVENT_READ, "sock")
        sel.register(wake_r, selectors.EVENT_READ, "winch")
        while True:
            for key, _ in sel.select():
                if key.data == "in":
                    data = os.read(0, 65536)
                    m = DETACH_CSI_U.search(data)
                    cut = min([i for i in (data.find(DETACH_KEY), m.start() if m else -1) if i >= 0], default=-1)
                    if cut >= 0:
                        before = data[:cut]
                        if before:
                            s.sendall(frame(b"i", before))
                        reason = "detach"
                        return reason
                    s.sendall(frame(b"i", data))
                elif key.data == "sock":
                    data = s.recv(65536)
                    if not data:
                        return reason
                    view = memoryview(data)
                    while view:
                        n = os.write(1, view)
                        view = view[n:]
                else:
                    os.read(wake_r, 64)
                    c, r = os.get_terminal_size()
                    s.sendall(frame(b"r", struct.pack(">HH", r, c)))
    finally:
        # 把 agent 打开的终端模式还原，免得回到 shell 后终端状态乱掉
        os.write(1, b"\x1b[?2004l\x1b[?1004l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[<99u"
                    b"\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(0, termios.TCSADRAIN, old_tty)
        signal.signal(signal.SIGWINCH, old_handler)
        s.close()
        os.close(wake_r)
        os.close(wake_w)
        print(f"\r\n[corral] {name}: {reason} (readonly={hello.get('readonly')})\r")


def cmd_attach(args):
    wait = "--wait" in args
    name = [a for a in args if a != "--wait"][0]
    if not os.isatty(0):
        die(EXIT_ERR, "attach needs a tty")
    while True:
        if wait:
            shown = False
            while connect(name) is None:
                if not shown:
                    sys.stdout.write(f"\x1b]2;waiting {name}\x07等待 {name} 出现…\r\n")
                    sys.stdout.flush()
                    shown = True
                time.sleep(0.3)
        reason = attach_once(name)
        if reason == "notfound" and not wait:
            die(EXIT_NOTFOUND, "not found")
        if not wait or reason == "detach":
            return


COMMANDS = {"start": cmd_start, "send": cmd_send, "keys": cmd_keys, "status": cmd_status, "wait": cmd_wait,
            "reply": cmd_reply, "read": cmd_read, "stop": cmd_stop, "ls": cmd_ls, "where": cmd_where,
            "attach": cmd_attach}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        die(EXIT_ERR, "usage: corral.py {" + ",".join(COMMANDS) + "} ...")
    COMMANDS[sys.argv[1]](sys.argv[2:])
