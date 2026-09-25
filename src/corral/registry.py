"""扫描状态目录：列出活着的 agent，顺手清掉栏位已死的残留。"""
import fcntl
import os

from corral import client, paths
from corral.errors import EXIT_INCOMPATIBLE, CorralError


def names():
    home = paths.state_home()
    found = []
    for root, dirs, files in os.walk(home):
        dirs.sort()
        if "lock" in files and root != home:
            found.append(os.path.relpath(root, home))
    return sorted(found)


def _cleanup(name):
    """栏位已死：没人拿着锁才清（正在启动的栏位拿着锁但还没开始监听）。返回是否清掉了。"""
    d = paths.pen_dir(name)
    lock_path = os.path.join(d, "lock")
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except FileNotFoundError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        for f in paths.INTERNAL_FILES:
            if f != "lock":
                try:
                    os.unlink(os.path.join(d, f))
                except FileNotFoundError:
                    pass
        os.unlink(lock_path)  # 拿着锁删：start 会发现锁住的文件已不在目录里，重新创建
    finally:
        os.close(fd)
    home = paths.state_home()
    while d != home:
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)
    return True


def list_agents():
    agents = []
    for name in names():
        try:
            st = client.request(name, "status", timeout=5.0)
        except CorralError as e:
            if e.exit_code == EXIT_INCOMPATIBLE:
                agents.append({"name": name, "incompatible": True, "proto": e.extra.get("proto")})
                continue
            st = None
        except Exception:  # noqa: BLE001  （某个栏位应答异常不影响列出其他的）
            st = None
        if st is None or not st.get("ok"):
            if not _cleanup(name):
                agents.append({"name": name, "starting": True})
            continue
        m = client.meta(name)
        agents.append({"name": name, "instance": st["instance"], "kind": m.get("kind"), "cwd": m.get("cwd"),
                       "started": st["started"], "labels": client.labels(name, st["instance"])})
    return agents
