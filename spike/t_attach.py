"""第 4 条：在伪终端里跑 corral attach，模拟窗口；杀掉 attach 后再接回来，检查 agent 和状态还在。

用法：python3 t_attach.py <名字>   （目标 agent 应是一个 sh）
"""
import os
import pty
import select
import signal
import struct
import fcntl
import termios
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = sys.argv[1]


def spawn_attach(rows, cols, *extra):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, os.path.join(HERE, "corral.py"), "attach", *extra, NAME])
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


def main():
    # 第一个「窗口」：30x100，设变量，看尺寸
    pid, fd = spawn_attach(30, 100)
    drain(fd, 1.0)
    os.write(fd, b"X=first-window; stty size\r")
    got = drain(fd, 1.0)
    print("win1 stty size ->", b"30 100" in got, repr(got[-80:]))

    # 模拟关窗口：给 attach 发 SIGHUP，再补一刀 SIGKILL
    os.kill(pid, signal.SIGHUP)
    time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    os.close(fd)

    # 第二个「窗口」：50x160，变量应该还在，尺寸应该跟着变
    pid2, fd2 = spawn_attach(50, 160)
    drain(fd2, 1.0)
    os.write(fd2, b"echo X=$X; stty size\r")
    got = drain(fd2, 1.0)
    print("win2 X kept ->", b"X=first-window" in got, "size 50 160 ->", b"50 160" in got)

    # 窗口改尺寸：attach 收到 SIGWINCH，应转给栏位
    fcntl.ioctl(fd2, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 150, 0, 0))
    time.sleep(0.3)
    os.write(fd2, b"stty size\r")
    got = drain(fd2, 1.0)
    print("win2 live resize 45 150 ->", b"45 150" in got)

    # 第三个「窗口」同时接入：应只读
    pid3, fd3 = spawn_attach(20, 80)
    hello3 = drain(fd3, 0.8)
    os.write(fd3, b"echo FROM-READONLY\r")
    time.sleep(0.5)
    os.write(fd2, b"echo check\r")
    got2 = drain(fd2, 1.0)
    print("readonly input ignored ->", b"FROM-READONLY" not in got2)

    # 第二个窗口按 Ctrl-] 退出接入
    os.write(fd2, b"\x1d")
    tail = drain(fd2, 1.0)
    _, status = os.waitpid(pid2, 0)
    print("detach key exit ->", os.waitstatus_to_exitcode(status), repr(tail[-60:]))
    os.kill(pid3, signal.SIGKILL)
    os.waitpid(pid3, 0)


main()
