"""试验钩子：照 corral 事件格式 1 记一行（corral status 照样能读），另把钩子原始输入放进 raw（长字段截短）。
不往标准输出写，退出码总是 0。"""
import json
import os
import sys
import time

FIELDS = ("session_id", "cwd", "source", "tool_name", "prompt", "last_assistant_message", "notification_type")


def short(v, n=200):
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + "…"
    if isinstance(v, dict):
        return {k: short(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [short(x, n) for x in v[:10]]
    return v


try:
    raw = sys.stdin.buffer.read()
    path = os.environ.get("CORRAL_EVENTS")
    if path:
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            payload = {}
        rec = {"v": 1, "t": time.time(), "ev": sys.argv[1] if len(sys.argv) > 1 else "",
               "inst": os.environ.get("CORRAL_INSTANCE", ""), "has_transcript": bool(payload.get("transcript_path"))}
        for k in FIELDS:
            if isinstance(payload.get(k), (str, int, float, bool)):
                rec[k] = payload[k]
        rec["raw"] = short({k: v for k, v in payload.items() if k not in ("transcript_path", "cwd")})
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
except Exception:
    pass
sys.exit(0)
