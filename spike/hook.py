"""corral 试验版钩子：把 agent 的钩子输入追加到事件文件。不往标准输出写任何东西。"""
import json
import os
import sys
import time

data = sys.stdin.buffer.read()
path = os.environ.get("CORRAL_EVENTS")
if path:
    try:
        payload = json.loads(data or b"null")
    except ValueError:
        payload = {"raw": data.decode("utf-8", "replace")}
    line = json.dumps({
        "t": time.time(),
        "ev": sys.argv[1] if len(sys.argv) > 1 else "",
        "inst": os.environ.get("CORRAL_INSTANCE"),
        "d": payload,
    }, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
