"""命令侧读事件文件：增量读（cursor）、只认主会话、状态机。

事件文件由各 agent 的钩子追加，长期运行会越来越大。cursor 记下读到的字节位置和读到这里为止算好的快照，
每次只读新增部分，只消费到最后一个完整换行（钩子可能正写到一半）。快照只取决于它记录的位置，
多个命令并发读写 cursor 时，旧快照覆盖新快照也不会算错，下一个命令最多多读一段。
"""
import hashlib
import json
import os

from corral.errors import EXIT_INCOMPATIBLE, CorralError

STATES = ("starting", "idle", "working", "blocked", "exiting")  # 认识的 agent；不认识的报 unknown
EVENT_FORMATS = (1,)   # 能读的事件格式版本：当前和上一个（DESIGN 5.4）
MAX_PENDING = 50       # 还认不出属于哪个会话的事件，最多暂存这么多
MAX_OTHER_SESSIONS = 50
MAX_INPUTS = 10
READ_BLOCK = 1024 * 1024
CURSOR_VERSION = 2


def digest(text):
    norm = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def fresh(instance):
    # fmt：这份快照是从哪个格式的事件算出来的（已消费事件里 v 的最大值），不是写 cursor 的 corral 的能力上限；
    # 否则事件全是 v1 的栏位被新命令读过一次，旧命令就再也读不动。还没消费任何事件时取本版本认识的最老格式。
    return {"cursor": CURSOR_VERSION, "fmt": min(EVENT_FORMATS), "inst": instance, "offset": 0,
            "main_session": None, "other_sessions": [],
            "pending": [], "state": "starting", "last_tool": None, "turn_started": None, "last_event": None,
            "last_event_t": None, "inputs": [], "input_count": 0, "reply": None, "reply_t": None}


def _same_dir(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (TypeError, ValueError):
        return False


def _update(snap, e):
    """主会话的一个事件怎么改状态。"""
    ev, t = e.get("ev"), e.get("t")
    snap["last_event"], snap["last_event_t"] = ev, t
    if ev == "SessionStart":
        if e.get("source") != "compact":
            snap["state"] = "idle"
    elif ev == "UserPromptSubmit":
        snap["state"], snap["turn_started"], snap["last_tool"] = "working", t, None
        snap["inputs"] = (snap["inputs"] + [{"t": t, "digest": digest(str(e.get("prompt", "")))}])[-MAX_INPUTS:]
        snap["input_count"] += 1
    elif ev in ("PreToolUse", "PostToolUse"):
        snap["state"] = "working"
        snap["last_tool"] = e.get("tool_name", snap["last_tool"])
    elif ev == "PermissionRequest":
        snap["state"] = "blocked"
        snap["last_tool"] = e.get("tool_name", snap["last_tool"])
    elif ev == "Notification":
        if e.get("notification_type") == "permission_prompt":
            snap["state"] = "blocked"
    elif ev in ("Stop", "StopFailure", "Interrupt"):
        snap["state"] = "idle"
        if ev == "Stop" and isinstance(e.get("last_assistant_message"), str):
            snap["reply"], snap["reply_t"] = e["last_assistant_message"], t
    elif ev == "SessionEnd":
        snap["state"] = "exiting"


def _apply(snap, e, cwd):
    sid = e.get("session_id")
    if e.get("ev") == "SessionStart":
        if e.get("has_transcript") and _same_dir(e.get("cwd"), cwd):
            if sid != snap["main_session"]:
                snap["main_session"] = sid
                if sid in snap["other_sessions"]:
                    snap["other_sessions"].remove(sid)
            _update(snap, e)
            mine = sorted((p for p in snap["pending"] if p.get("session_id") == sid), key=lambda p: p.get("t", 0))
            snap["pending"] = [p for p in snap["pending"] if p.get("session_id") != sid]
            for p in mine:
                _update(snap, p)
        else:
            if sid != snap["main_session"] and sid not in snap["other_sessions"]:
                snap["other_sessions"] = (snap["other_sessions"] + [sid])[-MAX_OTHER_SESSIONS:]
            snap["pending"] = [p for p in snap["pending"] if p.get("session_id") != sid]
        return
    if sid is None or sid == snap["main_session"]:
        if snap["main_session"] is not None:
            _update(snap, e)
    elif sid not in snap["other_sessions"]:
        snap["pending"] = (snap["pending"] + [e])[-MAX_PENDING:]


def _load_cursor(path, instance, size):
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return fresh(instance)
    if (not isinstance(snap, dict) or snap.get("cursor") != CURSOR_VERSION or snap.get("inst") != instance
            or not isinstance(snap.get("offset"), int) or snap["offset"] > size):
        return fresh(instance)
    # cursor 是各版本 corral 共用的：另一个版本读过之后把进度推到文件末尾，本版本就一行事件都读不到，
    # 逐行的格式检查形同虚设。所以 cursor 里记下快照是按哪个事件格式算的，不认识就直接拒绝——不能退回 fresh
    # 从头重读（几十 MB 白读一遍，最后还是在第一行拒绝）。不能靠 CURSOR_VERSION 顶替：只改事件格式时
    # CURSOR_VERSION 不变，之前就是这么漏的。没有 fmt 字段的 cursor 是加这个字段之前写的，那时只有格式 1，
    # 补上之后随下一次写回落盘。
    fmt = snap.get("fmt", 1)
    if fmt not in EVENT_FORMATS:
        raise CorralError(EXIT_INCOMPATIBLE, "incompatible",
                          f"event format version {fmt!r} is not supported by this corral "
                          f"(supported: {list(EVENT_FORMATS)}); stop and start the agent again")
    snap["fmt"] = fmt
    return snap


def _save_cursor(path, snap):
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(snap, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read(pen_dir, instance, cwd):
    """读到事件文件末尾，返回快照（并写回 cursor）。"""
    events_path = os.path.join(pen_dir, "events")
    cursor_path = os.path.join(pen_dir, "cursor")
    try:
        f = open(events_path, "rb")
    except FileNotFoundError:
        return fresh(instance)
    with f:
        size = os.fstat(f.fileno()).st_size
        snap = _load_cursor(cursor_path, instance, size)
        start = snap["offset"]
        f.seek(start)
        carry = b""
        while True:
            block = f.read(READ_BLOCK)
            if not block:
                break
            data = carry + block
            end = data.rfind(b"\n")
            if end < 0:
                carry = data
                continue
            for line in data[:end].split(b"\n"):
                _consume_line(snap, line, instance, cwd)
            snap["offset"] += end + 1
            carry = data[end + 1:]
    if snap["offset"] != start:
        _save_cursor(cursor_path, snap)
    return snap


def _consume_line(snap, line, instance, cwd):
    try:
        e = json.loads(line)
    except ValueError:
        return
    if not isinstance(e, dict) or e.get("inst") != instance:
        return
    if e.get("v") not in EVENT_FORMATS:
        raise CorralError(EXIT_INCOMPATIBLE, "incompatible",
                          f"event format version {e.get('v')!r} is not supported by this corral "
                          f"(supported: {list(EVENT_FORMATS)}); stop and start the agent again")
    snap["fmt"] = max(snap["fmt"], e["v"])
    _apply(snap, e, cwd)
