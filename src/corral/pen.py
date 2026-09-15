"""栏位：每个 agent 一个的常驻进程，持有 agent 的伪终端，只做字节级的事。

这个模块只在顶层 import（测试会检查）：栏位启动后不再从磁盘加载 corral 的代码，
corral 升级不影响正在跑的栏位。
"""
import base64
import errno
import fcntl
import json
import os
import pty
import selectors
import signal
import socket
import struct
import termios
import time

from corral import protocol, termmodes

DEFAULT_ROWS, DEFAULT_COLS = 40, 120
HUMAN_QUIET_SECONDS = 30.0   # 这么久内有人在接入窗口操作过，send 避让
MAX_RECENT_SENDS = 10
STOP_SIGNALS = {"HUP": signal.SIGHUP, "INT": signal.SIGINT, "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}
RING_MAX = 512 * 1024
CLIENT_OUT_MAX = 8 * 1024 * 1024


def write_private_json(path, obj):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(obj, ensure_ascii=False).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def reset_signals():
    """exec 前调用。被忽略的信号会随 exec 继承（Python 启动时忽略 SIGPIPE，栏位忽略 SIGHUP），
    shell 也无法恢复启动时就被忽略的信号，所以 agent 必须拿到默认处理和空的信号屏蔽。"""
    for sig in signal.valid_signals():
        if sig in (signal.SIGKILL, signal.SIGSTOP):
            continue
        try:
            if signal.getsignal(sig) == signal.SIG_IGN:
                signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
    signal.pthread_sigmask(signal.SIG_SETMASK, [])


def set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.inbuf = b""
        self.out = bytearray()
        self.mode = "handshake"   # handshake / closing / attached
        self.rows, self.cols = DEFAULT_ROWS, DEFAULT_COLS


class Pen:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg["dir"]
        self.sock_path = os.path.join(self.dir, "sock")
        self.sel = selectors.DefaultSelector()
        self.clients = {}
        self.ring = bytearray()
        self.term = termmodes.TerminalState()
        self.last_output = None
        self.started = time.time()
        self.agent_pid = None
        self.master = None
        self.exit_status = None
        self.exit_seen_at = None
        self.pty_open = True
        self.writer = None          # 可打字的接入者；其他接入者只读
        self.last_human_input = None
        self.inq = []               # 待写进伪终端的 [字节, 写完后等几秒, 写完后要应答的客户端]
        self.next_write = 0.0
        self.timers = []            # [时间, 函数]
        self.size = (DEFAULT_ROWS, DEFAULT_COLS)
        self.recent_sends = []      # [{"t", "digest"}]，用来判断输入事件是不是 send 送的
        self.stop_step = None       # stop 流程执行到哪一步（keys / SIGHUP / SIGTERM / SIGKILL）

    # ---- 启动

    def spawn_agent(self):
        """在伪终端里启动 agent。exec 失败时通过一个 exec 时自动关闭的管道把原因带回来。"""
        err_r, err_w = os.pipe()
        pid, master = pty.fork()
        if pid == 0:
            os.close(err_r)
            try:
                reset_signals()
                os.chdir(self.cfg["cwd"])
                os.execvpe(self.cfg["argv"][0], self.cfg["argv"], self.cfg["env"])
            except BaseException as e:  # noqa: BLE001
                os.write(err_w, f"{type(e).__name__}: {e}".encode())
            os._exit(127)
        os.close(err_w)
        failure = b""
        while True:
            chunk = os.read(err_r, 4096)
            if not chunk:
                break
            failure += chunk
        os.close(err_r)
        if failure:
            os.waitpid(pid, 0)
            os.close(master)
            return failure.decode("utf-8", "replace")
        self.agent_pid, self.master = pid, master
        set_winsize(master, DEFAULT_ROWS, DEFAULT_COLS)
        os.set_blocking(master, False)
        return None

    def listen(self):
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self.listener.bind(self.sock_path)
        finally:
            os.umask(old)
        os.chmod(self.sock_path, 0o600)
        self.listener.listen(64)
        self.listener.setblocking(False)

    def write_meta(self):
        write_private_json(os.path.join(self.dir, "meta.json"), {
            "name": self.cfg["name"], "instance": self.cfg["instance"], "proto": protocol.PROTOCOL_VERSION,
            "pen_pid": os.getpid(), "agent_pid": self.agent_pid, "cwd": self.cfg["cwd"],
            "argv": self.cfg["argv"], "kind": self.cfg["kind"], "started": self.started,
            "version": self.cfg["version"],
        })

    # ---- 应答

    def attached(self):
        return [c for c in self.clients.values() if c.mode == "attached"]

    def status(self):
        return {"ok": True, "proto": protocol.PROTOCOL_VERSION, "instance": self.cfg["instance"],
                "agent_pid": self.agent_pid, "pen_pid": os.getpid(), "started": self.started,
                "last_output": self.last_output, "title": self.term.title, "attached": len(self.attached()),
                "writer_attached": self.writer is not None, "last_human_input": self.last_human_input,
                "size": list(self.size), "bracketed_paste": self.term.modes.get("2004") == "h",
                "recent_sends": self.recent_sends}

    # ---- 伪终端尺寸和输入

    def resize(self, rows, cols, redraw=False):
        """改伪终端尺寸（agent 收到 SIGWINCH）。redraw：尺寸不变也要让 agent 重绘，先改小一列再改回来。"""
        self.size = (rows, cols)
        if redraw:
            set_winsize(self.master, rows, max(cols - 1, 1))
            self.timers.append([time.time() + 0.15, lambda: set_winsize(self.master, rows, cols)])
        else:
            set_winsize(self.master, rows, cols)

    def enqueue(self, data, delay_after=0.0, reply_to=None):
        self.inq.append([bytes(data), delay_after, reply_to])

    def on_pty_writable(self):
        item = self.inq[0]
        try:
            n = os.write(self.master, item[0])
        except BlockingIOError:
            return
        except OSError:
            n = len(item[0])  # agent 已经退出，丢掉
        item[0] = item[0][n:]
        if not item[0]:
            self.inq.pop(0)
            self.next_write = time.time() + item[1]
            if item[2] is not None and item[2].sock in self.clients:
                self.reply(item[2], {"ok": True})

    def send_to(self, c, data):
        c.out += data
        self._update_client_events(c)

    def reply(self, c, obj):
        obj.setdefault("proto", protocol.PROTOCOL_VERSION)
        self.send_to(c, protocol.encode_line(obj))
        c.mode = "closing"

    def handle_request(self, c, req):
        op = req.get("op")
        if op == "status":
            self.reply(c, self.status())
        elif op == "read":
            n = max(0, int(req.get("bytes", 16000)))
            data = bytes(self.ring[-n:]) if n else b""
            self.reply(c, {"ok": True, "data": base64.b64encode(data).decode()})
        elif op == "attach":
            self.attach(c, req)
        elif op in ("send", "keys"):
            self.write_chunks(c, req)
        elif op == "stop":
            self.stop(c, req)
        else:
            self.reply(c, {"ok": False, "error": "bad_op", "message": f"unknown op {op!r}"})

    def stop(self, c, req):
        """按调用方给的顺序让 agent 退出：每一步之后等一会儿，agent 还在就做下一步，最后总是 SIGKILL。
        由栏位执行，发 stop 的命令中途被杀掉也能走完。"""
        if self.stop_step is not None:
            self.reply(c, {"ok": True, "stopping": True})
            return
        steps = []
        try:
            for st in req.get("steps", []):
                wait = min(max(float(st.get("wait", 3)), 0.0), 60.0)
                if "keys" in st:
                    steps.append(("keys", base64.b64decode(st["keys"]), wait))
                else:
                    steps.append(("SIG" + st["signal"], STOP_SIGNALS[st["signal"]], wait))
        except (KeyError, TypeError, ValueError) as e:
            self.reply(c, {"ok": False, "error": "bad_request", "message": f"bad stop steps: {e}"})
            return
        steps.append(("SIGKILL", signal.SIGKILL, 0.0))
        self.run_stop_step(steps, 0)
        self.reply(c, {"ok": True})

    def run_stop_step(self, steps, i):
        if self.exit_status is not None or i >= len(steps):
            return
        label, what, wait = steps[i]
        self.stop_step = label
        if label == "keys":
            if self.pty_open:
                self.enqueue(what)
        else:
            try:
                os.killpg(self.agent_pid, what)
            except ProcessLookupError:
                pass
        self.timers.append([time.time() + wait, lambda: self.run_stop_step(steps, i + 1)])

    def write_chunks(self, c, req):
        """把几段字节依次写进伪终端，全部写完才应答。send 在写入这一刻检查最近有没有人工操作。"""
        try:
            chunks = [(base64.b64decode(ch["data"]), float(ch.get("delay", 0))) for ch in req["chunks"]]
        except (KeyError, TypeError, ValueError) as e:
            self.reply(c, {"ok": False, "error": "bad_request", "message": f"bad chunks: {e}"})
            return
        if not chunks:
            self.reply(c, {"ok": True})
            return
        if req["op"] == "send":
            now = time.time()
            if (not req.get("force") and self.last_human_input is not None
                    and now - self.last_human_input < HUMAN_QUIET_SECONDS):
                self.reply(c, {"ok": False, "error": "human_active", "last_human_input": self.last_human_input,
                               "message": "someone typed in an attached window recently"})
                return
            self.recent_sends = (self.recent_sends + [{"t": now, "digest": str(req.get("digest", ""))}])[
                -MAX_RECENT_SENDS:]
        c.mode = "waiting"
        for i, (data, delay) in enumerate(chunks):
            self.enqueue(data, delay, c if i == len(chunks) - 1 else None)

    def attach(self, c, req):
        try:
            rows, cols = int(req.get("rows", DEFAULT_ROWS)), int(req.get("cols", DEFAULT_COLS))
        except (TypeError, ValueError):
            rows, cols = DEFAULT_ROWS, DEFAULT_COLS
        c.rows, c.cols = min(max(rows, 1), 10000), min(max(cols, 1), 10000)
        readonly = self.writer is not None
        c.mode = "attached"
        self.send_to(c, protocol.encode_line({"ok": True, "proto": protocol.PROTOCOL_VERSION,
                                              "instance": self.cfg["instance"], "readonly": readonly}))
        # 先把 agent 打开过的终端模式告诉新窗口，再让 agent 按新窗口尺寸重绘
        self.send_to(c, self.term.replay())
        if not readonly:
            self.writer = c
            self.resize(c.rows, c.cols, redraw=True)

    def on_frames(self, c):
        frames, c.inbuf = protocol.decode_frames(c.inbuf)
        for typ, payload in frames:
            if c is not self.writer:
                continue
            if typ == protocol.FRAME_INPUT:
                if termmodes.is_human_input(payload):
                    self.last_human_input = time.time()
                self.enqueue(payload)
            elif typ == protocol.FRAME_RESIZE and len(payload) == 4:
                rows, cols = protocol.decode_resize(payload)
                c.rows, c.cols = max(rows, 1), max(cols, 1)
                self.resize(c.rows, c.cols)

    # ---- 事件循环

    def _update_client_events(self, c):
        events = selectors.EVENT_READ | (selectors.EVENT_WRITE if c.out else 0)
        try:
            self.sel.modify(c.sock, events, c)
        except (KeyError, ValueError):
            pass

    def drop(self, c):
        try:
            self.sel.unregister(c.sock)
        except (KeyError, ValueError):
            pass
        c.sock.close()
        self.clients.pop(c.sock, None)
        if self.writer is c:
            self.writer = next(iter(self.attached()), None)
            if self.writer is not None:
                self.resize(self.writer.rows, self.writer.cols, redraw=True)

    def on_output(self, data):
        self.last_output = time.time()
        self.ring.extend(data)
        if len(self.ring) > RING_MAX:
            del self.ring[:len(self.ring) - RING_MAX]
        self.term.feed(data)
        for c in self.attached():
            self.send_to(c, data)
            if len(c.out) > CLIENT_OUT_MAX:  # 接入窗口跟不上，断开它，不拖累 agent
                self.drop(c)

    def on_pty(self):
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError as e:
            if e.errno != errno.EIO:
                raise
            data = b""
        if data:
            self.on_output(data)
        else:
            self.sel.unregister(self.master)
            self.pty_open = False

    def on_accept(self):
        try:
            s, _ = self.listener.accept()
        except BlockingIOError:
            return
        s.setblocking(False)
        c = Client(s)
        self.clients[s] = c
        self.sel.register(s, selectors.EVENT_READ, c)

    def on_client(self, c, events):
        if events & selectors.EVENT_READ:
            try:
                data = c.sock.recv(65536)
            except BlockingIOError:
                data = None
            except ConnectionError:
                data = b""
            if data == b"":
                self.drop(c)
                return
            if data:
                c.inbuf += data
                if c.mode == "handshake" and b"\n" in c.inbuf:
                    line, c.inbuf = c.inbuf.split(b"\n", 1)
                    try:
                        req = json.loads(line)
                        if not isinstance(req, dict):
                            raise ValueError("request is not an object")
                        self.handle_request(c, req)
                    except (ValueError, TypeError) as e:
                        self.reply(c, {"ok": False, "error": "bad_request", "message": str(e)})
                elif c.mode == "handshake" and len(c.inbuf) > protocol.MAX_LINE:
                    self.drop(c)
                    return
                if c.mode == "attached":
                    self.on_frames(c)
        if events & selectors.EVENT_WRITE and c.sock in self.clients:
            try:
                n = c.sock.send(c.out)
            except BlockingIOError:
                n = 0
            except OSError:
                self.drop(c)
                return
            del c.out[:n]
            if not c.out and c.mode == "closing":
                self.drop(c)
            else:
                self._update_client_events(c)

    def check_child(self):
        if self.exit_status is not None:
            return
        try:
            pid, status = os.waitpid(self.agent_pid, os.WNOHANG)
        except ChildProcessError:
            pid, status = self.agent_pid, 0
        if pid == self.agent_pid:
            self.exit_status = status
            self.exit_seen_at = time.time()

    def finished(self):
        # agent 退出后再给 0.3 秒把伪终端里剩下的输出读完
        return self.exit_status is not None and (not self.pty_open or time.time() - self.exit_seen_at > 0.3)

    def loop(self):
        self.sel.register(self.master, selectors.EVENT_READ, "pty")
        self.sel.register(self.listener, selectors.EVENT_READ, "listen")
        while not self.finished():
            now = time.time()
            for t in [t for t in self.timers if t[0] <= now]:
                self.timers.remove(t)
                try:
                    t[1]()
                except OSError:
                    pass
            if self.pty_open:
                want = selectors.EVENT_READ
                if self.inq and now >= self.next_write:
                    want |= selectors.EVENT_WRITE
                self.sel.modify(self.master, want, "pty")
            deadlines = [t[0] for t in self.timers] + ([self.next_write] if self.inq else [])
            timeout = min([0.2] + [max(0.0, d - now) for d in deadlines])
            for key, events in self.sel.select(timeout):
                if key.data == "pty":
                    if events & selectors.EVENT_READ:
                        self.on_pty()
                    if events & selectors.EVENT_WRITE and self.pty_open and self.inq:
                        self.on_pty_writable()
                elif key.data == "listen":
                    self.on_accept()
                else:
                    self.on_client(key.data, events)
            self.check_child()

    def shutdown(self):
        code = None if self.exit_status is None else os.waitstatus_to_exitcode(self.exit_status)
        write_private_json(os.path.join(self.dir, "exit.json"),
                           {"instance": self.cfg["instance"], "code": code, "t": time.time(),
                            "stop_step": self.stop_step})
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        for c in list(self.clients.values()):
            try:
                c.sock.setblocking(True)
                c.sock.settimeout(0.5)
                if c.out:
                    c.sock.sendall(c.out)
            except OSError:
                pass
            c.sock.close()


def run(cfg, ready_fd):
    """栏位主函数：在已经脱离的孙进程里调用。把启动结果写到 ready_fd 后关闭它。"""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    pen = Pen(cfg)
    failure = pen.spawn_agent()
    if failure:
        os.write(ready_fd, protocol.encode_line({"ok": False, "error": "exec_failed",
                                                 "message": f"cannot run {cfg['argv'][0]!r}: {failure}"}))
        os.close(ready_fd)
        return 1
    pen.listen()
    pen.write_meta()
    os.write(ready_fd, protocol.encode_line({"ok": True, "agent_pid": pen.agent_pid, "pen_pid": os.getpid()}))
    os.close(ready_fd)
    try:
        pen.loop()
    finally:
        if pen.exit_status is None:
            # 栏位自己出错：不能留下没人管的 agent
            try:
                os.killpg(pen.agent_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        pen.shutdown()
    return 0
