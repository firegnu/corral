"""corral attach：把当前终端接到栏位上，原样转发字节。"""
import json
import os
import selectors
import signal
import sys
import termios
import time
import tty

from corral import client, protocol, termmodes
from corral.errors import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_OK, CorralError

TITLE_PUSH = b"\x1b[22;0t"
TITLE_POP = b"\x1b[23;0t"
WAIT_POLL = 0.3


def write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def terminal_size():
    try:
        cols, rows = os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        return 40, 120
    return rows, cols


def attach_once(name, sock):
    """接入一次。返回 'detach'（按了退出键）、'exited'（agent 退出）或 'closed'（终端关了）。"""
    rows, cols = terminal_size()
    sock.settimeout(None)
    sock.sendall(protocol.request_line("attach", rows=rows, cols=cols))
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return "exited"
        buf += chunk
    line, rest = buf.split(b"\n", 1)
    hello = json.loads(line)
    client.check_proto(name, hello.get("proto"))
    if not hello.get("ok"):
        raise CorralError(EXIT_ERROR, hello.get("error", "attach_failed"), hello.get("message", ""))

    term = termmodes.TerminalState()  # 跟踪这个窗口实际收到的模式，退出时按它还原
    stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    old_winch = signal.signal(signal.SIGWINCH, lambda *_: os.write(wake_w, b"w"))
    old_tty = termios.tcgetattr(stdin)
    reason = "exited"
    try:
        tty.setraw(stdin)
        write_all(stdout, TITLE_PUSH + b"\x1b]2;" + name.encode() + b"\x07")
        if rest:
            term.feed(rest)
            write_all(stdout, rest)
        sel = selectors.DefaultSelector()
        sel.register(stdin, selectors.EVENT_READ, "stdin")
        sel.register(sock, selectors.EVENT_READ, "sock")
        sel.register(wake_r, selectors.EVENT_READ, "winch")
        while True:
            for key, _ in sel.select():
                if key.data == "stdin":
                    data = os.read(stdin, 65536)
                    if not data:
                        reason = "closed"
                        return reason
                    cut = termmodes.find_detach(data)
                    if cut is not None:
                        if cut:
                            sock.sendall(protocol.encode_frame(protocol.FRAME_INPUT, data[:cut]))
                        reason = "detach"
                        return reason
                    sock.sendall(protocol.encode_frame(protocol.FRAME_INPUT, data))
                elif key.data == "sock":
                    try:
                        data = sock.recv(65536)
                    except ConnectionError:
                        data = b""
                    if not data:
                        return reason
                    term.feed(data)
                    write_all(stdout, data)
                else:
                    os.read(wake_r, 64)
                    r, c = terminal_size()
                    sock.sendall(protocol.encode_resize(r, c))
    finally:
        try:
            write_all(stdout, term.restore() + TITLE_POP)
        except OSError:
            pass
        termios.tcsetattr(stdin, termios.TCSADRAIN, old_tty)
        signal.signal(signal.SIGWINCH, old_winch)
        sock.close()
        os.close(wake_r)
        os.close(wake_w)
        if reason == "detach":
            sys.stderr.write(f"\r\n[corral] detached from {name}; it keeps running\r\n")
        elif reason == "exited":
            sys.stderr.write(f"\r\n[corral] {name} exited\r\n")


def run(name, wait):
    if not wait:
        sock = client.connect(name)
        if sock is None:
            raise client.not_found(name)
        if not os.isatty(sys.stdin.fileno()) or not os.isatty(sys.stdout.fileno()):
            sock.close()
            raise CorralError(EXIT_ERROR, "not_a_tty", "attach needs a terminal on stdin and stdout")
        reason = attach_once(name, sock)
        return EXIT_OK if reason in ("detach", "closed") else EXIT_NOT_FOUND
    if not os.isatty(sys.stdin.fileno()) or not os.isatty(sys.stdout.fileno()):
        raise CorralError(EXIT_ERROR, "not_a_tty", "attach needs a terminal on stdin and stdout")
    try:
        while True:
            sock = client.connect(name)
            if sock is None:
                sys.stderr.write(f"\x1b]2;waiting for {name}\x07waiting for {name} ...\r\n")
                sys.stderr.flush()
                while sock is None:
                    time.sleep(WAIT_POLL)
                    sock = client.connect(name)
            if attach_once(name, sock) in ("detach", "closed"):
                return EXIT_OK
    except KeyboardInterrupt:
        return EXIT_OK
