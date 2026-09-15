"""corral 钩子：把 agent 钩子的输入整理成一行追加到事件文件。

start 时复制到每个 agent 的状态目录，由 /usr/bin/python3 -I -S 运行，不依赖 corral 仓库和任何特定的 Python。
要求：Python 3.9 兼容；只用 json/os/sys/time；任何情况下都不往标准输出写、退出码为 0
（agent 会把某些钩子的标准输出加进对话，非零退出码可能被当成阻止操作）。
"""
import json
import os
import sys
import time

FORMAT = 1
FIELDS = ("session_id", "cwd", "source", "tool_name", "prompt", "last_assistant_message", "notification_type")


def main():
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    path = os.environ.get("CORRAL_EVENTS")
    if not path:
        return
    try:
        payload = json.loads(raw.decode("utf-8") or "null")
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    record = {
        "v": FORMAT,
        "t": time.time(),
        "ev": sys.argv[1] if len(sys.argv) > 1 else "",
        "inst": os.environ.get("CORRAL_INSTANCE", ""),
        "has_transcript": bool(payload.get("transcript_path")),
    }
    for key in FIELDS:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            record[key] = value
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


try:
    main()
except Exception:
    pass
sys.exit(0)
