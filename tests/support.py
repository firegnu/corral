"""测试共用：把 src 加进路径，提供跑命令的帮助函数。"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BIN = os.path.join(ROOT, "bin", "corral")

if SRC not in sys.path:
    sys.path.insert(0, SRC)


def short_tmpdir():
    """状态目录放短路径下（macOS 上 socket 路径不能超过 104 字节），测试进程退出时删掉。"""
    path = tempfile.mkdtemp(prefix="crt-", dir="/tmp")
    atexit.register(shutil.rmtree, path, True)
    return path


_fast_shell = None


def fast_shell():
    """极简的假登录 shell：不读任何配置，直接执行 -c 的脚本。测试默认用它，不依赖、不触发人的 shell 配置。"""
    global _fast_shell
    if _fast_shell is None:
        d = short_tmpdir()
        _fast_shell = os.path.join(d, "fastshell")
        with open(_fast_shell, "w") as f:
            f.write('#!/bin/sh\neval "$4"\n')
        os.chmod(_fast_shell, 0o700)
    return _fast_shell


_fake_agents = {}


def fake_agent(flavor):
    """生成名为 claude / codex 的假 agent 可执行文件，用当前 Python 运行 tests/fake_agents/fake_agent.py。"""
    if flavor not in _fake_agents:
        d = os.path.join(short_tmpdir(), flavor)
        os.makedirs(d)
        path = os.path.join(d, flavor)
        with open(path, "w") as f:
            f.write(f"#!{sys.executable}\nimport runpy, sys\n"
                    f"runpy.run_path({os.path.join(ROOT, 'tests', 'fake_agents', 'fake_agent.py')!r}, "
                    f"init_globals={{'FAKE_FLAVOR': {flavor!r}}}, run_name='__main__')\n")
        os.chmod(path, 0o700)
        _fake_agents[flavor] = path
    return _fake_agents[flavor]


def run_cli(args, env=None, home=None, timeout=30, umask=None):
    """跑 bin/corral，返回 (退出码, 解析后的 JSON 或原始输出)。"""
    full_env = dict(os.environ)
    full_env.pop("CODEX_SANDBOX", None)
    full_env["SHELL"] = fast_shell()
    if home is not None:
        full_env["CORRAL_HOME"] = home
    if env:
        full_env.update(env)
    preexec = (lambda: os.umask(umask)) if umask is not None else None
    proc = subprocess.run([sys.executable, BIN, *args], capture_output=True, text=True,
                          env=full_env, timeout=timeout, preexec_fn=preexec)
    out = proc.stdout
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        pass
    return proc.returncode, out


def wait_until(fn, timeout=10.0, interval=0.05):
    """反复调用 fn，直到返回真值；超时返回最后一次的值。"""
    import time
    end = time.time() + timeout
    while True:
        value = fn()
        if value or time.time() >= end:
            return value
        time.sleep(interval)


def read_meta(home, name):
    with open(os.path.join(home, name, "meta.json")) as f:
        return json.load(f)


def kill_all(home):
    """测试收尾：直接杀掉 home 下所有栏位和 agent。"""
    import signal
    for root, _dirs, files in os.walk(home):
        if "meta.json" not in files:
            continue
        try:
            with open(os.path.join(root, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        for kill in (lambda: os.killpg(meta["agent_pid"], signal.SIGKILL),
                     lambda: os.kill(meta["pen_pid"], signal.SIGKILL)):
            try:
                kill()
            except (ProcessLookupError, PermissionError, KeyError):
                pass
