"""agent 的环境：从用户的登录 shell 重建，兜底剥会话变量，最后加上 corral 的身份变量。

用最小环境启动登录 shell，调用方的环境（包括它所在会话的变量、临时加的 PATH）根本进不来；
得到的是用户自己新开终端时的环境（shell 配置文件里给 agent 设的变量都在）。
"""
import os
import pwd
import signal
import subprocess

BEGIN = "__CORRAL_ENV_BEGIN__"
END = "__CORRAL_ENV_END__"
CAPTURE_SCRIPT = f"printf '\\n{BEGIN}\\n'; /usr/bin/env -0; printf '\\n{END}\\n'"
DEFAULT_TIMEOUT = 10.0
DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

MINIMAL_KEYS = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "SSH_AUTH_SOCK")
WHITELIST_KEYS = MINIMAL_KEYS + ("PATH", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")

# shell 自己维护、换个进程就不对的变量
SHELL_INTERNAL = {"SHLVL", "PWD", "OLDPWD", "_"}
# 会话标识变量：只标识「调用方所在的那个会话」，传下去会让新 agent 冒充或串到那个会话
SESSION_PREFIXES = ("CORRAL_", "CODEX_")
SESSION_KEEP = {"CODEX_HOME"}  # 用户配置，不是会话标识
SESSION_KEYS = {
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_EXECPATH", "TMUX", "TMUX_PANE", "STY", "WINDOW", "ZELLIJ", "ZELLIJ_SESSION_NAME", "ZELLIJ_PANE_ID",
    "TERM_SESSION_ID", "ITERM_SESSION_ID", "KITTY_WINDOW_ID", "KITTY_PID", "KITTY_LISTEN_ON", "WEZTERM_PANE",
    "WEZTERM_UNIX_SOCKET", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
}


def user_shell():
    return os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"


def minimal_env():
    env = {k: os.environ[k] for k in MINIMAL_KEYS if os.environ.get(k)}
    pw = pwd.getpwuid(os.getuid())
    env.setdefault("HOME", pw.pw_dir)
    env.setdefault("USER", pw.pw_name)
    env.setdefault("LOGNAME", pw.pw_name)
    env["SHELL"] = user_shell()
    env["PATH"] = DEFAULT_PATH
    return env


def whitelist_env():
    env = {k: v for k, v in os.environ.items() if k in WHITELIST_KEYS or k.startswith("LC_")}
    env.setdefault("PATH", DEFAULT_PATH)
    env["SHELL"] = user_shell()
    return env


def parse_capture(output):
    begin = output.find(f"\n{BEGIN}\n".encode())
    end = output.find(f"\n{END}\n".encode(), begin + 1)
    if begin < 0 or end < 0:
        return None
    body = output[begin + len(BEGIN) + 2:end]
    env = {}
    for item in body.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep and key:
            env[os.fsdecode(key)] = os.fsdecode(value)
    return env or None


def login_shell_env(timeout):
    """返回 (环境, 失败原因)。"""
    shell = user_shell()
    try:
        proc = subprocess.Popen([shell, "-l", "-i", "-c", CAPTURE_SCRIPT], env=minimal_env(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True, cwd=minimal_env()["HOME"])
    except OSError as e:
        return None, f"cannot run login shell {shell}: {e}"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return None, f"login shell {shell} did not finish within {timeout:g}s"
    env = parse_capture(out)
    if env is None:
        return None, f"login shell {shell} did not print its environment (exit code {proc.returncode})"
    return env, None


def strip_session(env):
    return {k: v for k, v in env.items()
            if k not in SHELL_INTERNAL and k not in SESSION_KEYS
            and (k in SESSION_KEEP or not k.startswith(SESSION_PREFIXES))}


def build(name, instance, events, home, extra, timeout=DEFAULT_TIMEOUT):
    """返回 (agent 的环境, 警告列表)。"""
    warnings = []
    base, failure = login_shell_env(timeout)
    if base is None:
        warnings.append(f"{failure}; using a minimal environment instead")
        base = whitelist_env()
    env = strip_session(base)
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    env.update(extra)
    env.update(CORRAL_NAME=name, CORRAL_INSTANCE=instance, CORRAL_EVENTS=events, CORRAL_HOME=home)
    return env, warnings
