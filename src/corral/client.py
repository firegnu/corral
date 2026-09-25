"""命令侧：按名字连栏位、发请求、处理「不存在」。"""
import fcntl
import json
import os
import socket
import time

from corral import paths, protocol
from corral.errors import EXIT_ERROR, EXIT_INCOMPATIBLE, EXIT_NOT_FOUND, CorralError


def connect(name, timeout=5.0):
    path = paths.sock_path(name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except OSError:
        s.close()
        return None
    return s


def read_line(s):
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > protocol.MAX_LINE:
            raise CorralError(EXIT_ERROR, "bad_reply", "reply from pen is too long")
    line, _, rest = buf.partition(b"\n")
    return line, rest


def check_proto(name, proto):
    if proto not in protocol.SUPPORTED_PROTOCOLS:
        raise CorralError(EXIT_INCOMPATIBLE, "incompatible",
                          f"{name} runs pen protocol {proto!r}, this corral supports "
                          f"{list(protocol.SUPPORTED_PROTOCOLS)}; stop it with a matching corral and start again",
                          name=name, proto=proto)


def request(name, op, timeout=30.0, **fields):
    """发一个请求，返回应答；栏位不在返回 None；栏位协议版本不兼容时报 incompatible。"""
    s = connect(name)
    if s is None:
        return None
    m = meta(name)
    if "proto" in m:  # 先看栏位写下的协议版本，不认识就不发请求
        try:
            check_proto(name, m["proto"])
        except CorralError:
            s.close()
            raise
    try:
        s.settimeout(timeout)
        s.sendall(protocol.request_line(op, **fields))
        line, _ = read_line(s)
    except OSError:
        return None
    finally:
        s.close()
    if not line:
        return None
    try:
        reply = json.loads(line)
    except ValueError:
        raise CorralError(EXIT_ERROR, "bad_reply", "pen sent a malformed reply") from None
    check_proto(name, reply.get("proto"))
    return reply


def read_json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def not_found(name):
    exited = read_json_file(os.path.join(paths.pen_dir(name), "exit.json"))
    return CorralError(EXIT_NOT_FOUND, "not_found", f"{name} is not running", name=name, exited=exited)


def require(name, op, **fields):
    reply = request(name, op, **fields)
    if reply is None:
        raise not_found(name)
    if not reply.get("ok"):
        raise CorralError(EXIT_ERROR, reply.get("error", "pen_error"), reply.get("message", ""), name=name)
    return reply


def wait_gone(name, timeout):
    """等到栏位真正退出：连不上，并且文件锁已经释放（之后立刻同名 start 不会撞锁）。"""
    lock_path = os.path.join(paths.pen_dir(name), "lock")
    deadline = time.time() + timeout
    while True:
        s = connect(name, timeout=1.0)
        if s is None:
            try:
                fd = os.open(lock_path, os.O_RDWR)
            except FileNotFoundError:
                return True
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                pass
            finally:
                os.close(fd)
        else:
            s.close()
        if time.time() >= deadline:
            return False
        time.sleep(0.05)


def meta(name):
    return read_json_file(os.path.join(paths.pen_dir(name), "meta.json")) or {}


def labels(name, instance):
    """start --label 记下的标签；不是这个实例的（同名重开前留下的）不算。"""
    saved = read_json_file(os.path.join(paths.pen_dir(name), "labels.json")) or {}
    found = saved.get("labels") if saved.get("instance") == instance else None
    return found if isinstance(found, dict) else {}
