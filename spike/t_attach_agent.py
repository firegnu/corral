"""第 4 条（真实 agent）：接入 → 在接入窗口里打字提交 → 杀掉接入 → 换尺寸再接入 → 退出接入 → 用 send 验证对话还在。

用法：python3 t_attach_agent.py <名字>   （目标 agent 应处于 idle）
"""
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORRAL = os.path.join(HERE, "corral.py")
NAME = sys.argv[1]


def corral(*args):
    r = subprocess.run([sys.executable, CORRAL, *args], capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout.startswith("{") else r.stdout


def spawn_attach(rows, cols):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, CORRAL, "attach", NAME])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    return pid, fd


def drain(fd, secs):
    buf = b""
    end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
        if not r:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


def wait_idle(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = corral("status", NAME)
        if st.get("state") == "idle":
            return st
        time.sleep(0.3)
    return corral("status", NAME)


pid, fd = spawn_attach(40, 120)
first = drain(fd, 2.0)
print("attach#1 redraw bytes:", len(first))
os.write(fd, "只回复 PING".encode())
time.sleep(0.4)
os.write(fd, b"\r")
time.sleep(1.5)
st = corral("status", NAME)
print("typed via attach -> state:", st.get("state"), "last_event:", st.get("last_event"))
wait_idle()
drain(fd, 1.0)
print("reply after attach typing:", corral("reply", NAME).get("text"))

os.kill(pid, signal.SIGHUP)
time.sleep(0.3)
try:
    os.kill(pid, signal.SIGKILL)
except ProcessLookupError:
    pass
os.waitpid(pid, 0)
os.close(fd)
st = corral("status", NAME)
print("after killing attach -> alive:", st.get("ok"), "state:", st.get("state"), "attached:", st.get("attached"))

pid, fd = spawn_attach(30, 90)
second = drain(fd, 2.5)
print("attach#2 redraw bytes:", len(second), "contains PING:", "PING".encode() in second)
os.write(fd, b"\x1d")
drain(fd, 1.0)
_, status = os.waitpid(pid, 0)
print("detach exit:", os.waitstatus_to_exitcode(status), "status attached:", corral("status", NAME).get("attached"))

print("send after detach:", corral("send", NAME, "我上一句让你回复哪个词？只回复那个词").get("confirmed"))
wait_idle()
print("reply:", corral("reply", NAME).get("text"))
