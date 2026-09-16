"""命令行入口：参数解析、JSON 输出、退出码。"""
import argparse
import base64
import json
import os
import sys
import time

from corral import (__version__, agents, attach, client, errors, events, paths, registry, sandbox, skills, spawn,
                    termmodes)
from corral.errors import (EXIT_ERROR, EXIT_HUMAN_ACTIVE, EXIT_NOT_DELIVERED, EXIT_NOT_IDLE, EXIT_OK, EXIT_TIMEOUT,
                           CorralError)

ENTER_DELAY = 0.3       # 粘贴完隔一小段再送回车，避免回车被当成粘贴内容
WAIT_POLL = 0.2
WAIT_STABLE = 0.5       # 会话开始和输入事件可能相隔几十毫秒先后到，idle / blocked 要稳定这么久才算
HUMAN_SOURCE_WINDOW = 2.0
KEYS = {"enter": b"\r", "esc": b"\x1b", "tab": b"\t", "backspace": b"\x7f", "space": b" ",
        "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
        "ctrl-c": b"\x03", "ctrl-d": b"\x04"}

# 不碰 agent 和状态目录的命令，在沙箱里也照常可用
SANDBOX_EXEMPT = {"guide"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CorralError(EXIT_ERROR, "usage", message)


def build_parser():
    p = _Parser(prog="corral", add_help=True)
    p.add_argument("--version", action="store_true")
    sub = p.add_subparsers(dest="command", parser_class=_Parser)

    s = sub.add_parser("start")
    s.add_argument("name")
    s.add_argument("--cwd")
    s.add_argument("--unique", action="store_true")
    s.add_argument("--prompt")
    s.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")

    s = sub.add_parser("send")
    s.add_argument("name")
    s.add_argument("text")
    s.add_argument("--force", action="store_true")
    s.add_argument("--timeout", type=float, default=15.0)

    s = sub.add_parser("keys")
    s.add_argument("name")
    s.add_argument("keys", nargs="+")

    for cmd in ("status", "reply", "where"):
        sub.add_parser(cmd).add_argument("name")

    s = sub.add_parser("wait")
    s.add_argument("name")
    s.add_argument("--timeout", type=float, default=600.0)
    s.add_argument("--quiet", type=float)

    sub.add_parser("ls")

    s = sub.add_parser("read")
    s.add_argument("name")
    s.add_argument("--bytes", type=int, default=16000)

    s = sub.add_parser("attach")
    s.add_argument("name")
    s.add_argument("--wait", action="store_true")

    s = sub.add_parser("stop")
    s.add_argument("name")
    s.add_argument("--timeout", type=float, default=90.0)  # 要盖住最长的退出序列（Codex：0.3 + 60 + 3 秒）

    sub.add_parser("guide")

    s = sub.add_parser("install-skills")
    s.add_argument("--target", choices=("all", "claude", "codex"), default="all")
    s.add_argument("--remove", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--project")
    return p


def _split_agent_command(argv):
    """start 的 agent 命令跟在第一个 -- 后面，原样保留，不交给 argparse。"""
    if argv and argv[0] == "start" and "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, None


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def cmd_start(args, agent_command):
    emit(spawn.start(args.name, args.cwd, agent_command, unique=args.unique, env_pairs=args.env,
                     prompt=args.prompt))
    return EXIT_OK


def agent_status(name):
    """栏位的终端层信息 + 事件算出的状态。给 status / wait / send 共用。"""
    st = client.require(name, "status")
    m = client.meta(name)
    now = time.time()
    result = {"ok": True, "name": name, "instance": st["instance"], "kind": m.get("kind"), "proto": st["proto"],
              "state": "unknown", "last_tool": None, "turn_started": None, "last_event": None,
              "last_event_at": None, "last_input_at": None, "last_input_source": None,
              "title": st["title"], "last_output": st["last_output"],
              "idle_for": None if st["last_output"] is None else round(now - st["last_output"], 3),
              "attached": st["attached"], "last_human_input": st["last_human_input"], "started": st["started"]}
    if m.get("kind") in agents.ADAPTERS:
        snap = events.read(paths.pen_dir(name), st["instance"], m.get("cwd"))
        last_input = snap["inputs"][-1] if snap["inputs"] else None
        state = snap["state"]
        if m.get("has_prompt") and snap["input_count"] == 0 and state == "idle":
            state = "starting"  # 用 --prompt 启动、首句还没提交：不能让调用方以为已经答完
        result.update(state=state, last_tool=snap["last_tool"], turn_started=snap["turn_started"],
                      last_event=snap["last_event"], last_event_at=snap["last_event_t"],
                      last_input_at=last_input and last_input["t"],
                      last_input_source=input_source(last_input, st))
        result["_snapshot"] = snap
        result["_pen"] = st
    return result


def input_source(last_input, pen_status):
    """最近一次输入是谁给的：send 送的 / 接入窗口里的人 / agent 自己。"""
    if last_input is None:
        return None
    for sent in pen_status.get("recent_sends", []):
        if sent["digest"] == last_input["digest"] and sent["t"] <= last_input["t"]:
            return "send"
    human = pen_status.get("last_human_input")
    if human is not None and last_input["t"] - HUMAN_SOURCE_WINDOW <= human <= last_input["t"] + 0.5:
        return "human"
    return "agent"


def public(status):
    return {k: v for k, v in status.items() if not k.startswith("_")}


def cmd_status(args):
    emit(public(agent_status(args.name)))
    return EXIT_OK


def cmd_send(args):
    st = agent_status(args.name)
    known = st["kind"] in agents.ADAPTERS
    if known and st["state"] != "idle":
        raise CorralError(EXIT_NOT_IDLE, "not_idle", f"{args.name} is {st['state']}, not idle",
                          name=args.name, state=st["state"])
    body = args.text.encode()
    if known and st["_pen"]["bracketed_paste"]:
        body = b"\x1b[200~" + body + b"\x1b[201~"
    t0 = time.time()
    reply = client.request(args.name, "send", digest=events.digest(args.text), force=args.force, chunks=[
        {"data": base64.b64encode(body).decode(), "delay": ENTER_DELAY},
        {"data": base64.b64encode(b"\r").decode()}])
    if reply is None:
        raise client.not_found(args.name)
    if not reply.get("ok"):
        if reply.get("error") == "human_active":
            raise CorralError(EXIT_HUMAN_ACTIVE, "human_active", reply.get("message", ""), name=args.name,
                              last_human_input=reply.get("last_human_input"))
        raise CorralError(EXIT_ERROR, reply.get("error", "pen_error"), reply.get("message", ""), name=args.name)
    if not known:
        emit({"ok": True, "name": args.name, "instance": st["instance"], "confirmed": False})
        return EXIT_OK
    want = events.digest(args.text)
    deadline = time.time() + args.timeout
    while True:
        snap = events.read(paths.pen_dir(args.name), st["instance"], client.meta(args.name).get("cwd"))
        merged = None
        if any(i["digest"] == want and i["t"] >= t0 for i in snap["inputs"]):
            merged = False
        elif (snap["inputs"] and snap["inputs"][-1]["t"] >= t0 and snap.get("last_prompt")
              and events.normalize(args.text) and events.normalize(args.text) in events.normalize(snap["last_prompt"])):
            merged = True  # 输入框里原有没提交的文字，和送出的一起提交了：agent 收到了，不能让调用方重送
        if merged is not None:
            emit({"ok": True, "name": args.name, "instance": st["instance"], "confirmed": True,
                  "merged_with_draft": merged, "latency": round(time.time() - t0, 3)})
            return EXIT_OK
        if time.time() >= deadline:
            # 不补发任何按键：此刻屏幕上可能是菜单或对话框（DESIGN 第 12 节难点 6）
            raise CorralError(EXIT_NOT_DELIVERED, "not_delivered",
                              f"no input event for the sent text within {args.timeout:g}s",
                              name=args.name, instance=st["instance"])
        time.sleep(0.1)


def cmd_keys(args):
    chunks = []
    for key in args.keys:
        data = key[5:].encode() if key.startswith("text:") else KEYS.get(key)
        if data is None:
            raise CorralError(EXIT_ERROR, "usage", f"unknown key {key!r}; known: {sorted(KEYS)} or text:<literal>")
        chunks.append({"data": base64.b64encode(data).decode(), "delay": 0.05})
    client.require(args.name, "keys", chunks=chunks)
    emit({"ok": True, "name": args.name})
    return EXIT_OK


def cmd_wait(args):
    deadline = time.time() + args.timeout
    stable = None
    while True:
        st = agent_status(args.name)
        if st["kind"] not in agents.ADAPTERS:
            emit({**public(st), "result": "unknown"})
            return EXIT_OK
        key = (st["state"], st["last_event"], st["last_event_at"])
        if st["state"] in ("idle", "blocked"):
            if stable and stable[0] == key and time.time() - stable[1] >= WAIT_STABLE:
                emit({**public(st), "result": st["state"]})
                return EXIT_OK
            if not stable or stable[0] != key:
                stable = (key, time.time())
        else:
            stable = None
            if st["state"] == "working" and args.quiet is not None:
                last_activity = max(st["last_output"] or 0, st["last_event_at"] or 0)
                if time.time() - last_activity >= args.quiet:
                    emit({**public(st), "result": "stopped-quiet"})
                    return EXIT_OK
        if time.time() >= deadline:
            hint = (" (start not finished: the agent may be showing a dialog; attach to look)"
                    if st["state"] == "starting" else "")
            raise CorralError(EXIT_TIMEOUT, "timeout", f"{args.name} still {st['state']} after {args.timeout:g}s{hint}",
                              **public(st))
        time.sleep(WAIT_POLL)


def cmd_reply(args):
    st = agent_status(args.name)
    snap = st.get("_snapshot")
    if not snap or snap["reply"] is None:
        raise CorralError(EXIT_ERROR, "no_reply", f"{args.name} has no finished turn with a reply yet",
                          name=args.name)
    emit({"ok": True, "name": args.name, "instance": st["instance"], "text": snap["reply"], "at": snap["reply_t"]})
    return EXIT_OK


def cmd_stop(args):
    st = client.require(args.name, "status")
    kind = client.meta(args.name).get("kind")
    client.require(args.name, "stop", steps=agents.quit_steps(kind))
    if not client.wait_gone(args.name, args.timeout):
        raise CorralError(EXIT_TIMEOUT, "timeout", f"{args.name} did not exit within {args.timeout:g}s; "
                          f"the pen keeps escalating and ends with SIGKILL", name=args.name)
    info = client.read_json_file(os.path.join(paths.pen_dir(args.name), "exit.json")) or {}
    if info.get("instance") != st["instance"]:
        info = {}
    emit({"ok": True, "name": args.name, "instance": st["instance"], "exit_code": info.get("code"),
          "stopped_by": info.get("stop_step")})
    return EXIT_OK


def cmd_where(args):
    st = client.require(args.name, "status")
    m = client.meta(args.name)
    emit({"ok": True, "name": args.name, "instance": st["instance"], "kind": m.get("kind"), "cwd": m.get("cwd"),
          "agent_pid": st["agent_pid"], "started": st["started"]})
    return EXIT_OK


def cmd_read(args):
    reply = client.require(args.name, "read", bytes=args.bytes)
    sys.stdout.write(termmodes.strip_controls(base64.b64decode(reply["data"])))
    return EXIT_OK


def cmd_ls(args):
    emit({"ok": True, "agents": registry.list_agents()})
    return EXIT_OK


GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AGENT_USAGE.md")


def cmd_guide(args):
    with open(GUIDE_PATH, encoding="utf-8") as f:
        sys.stdout.write(f.read())
    return EXIT_OK


def cmd_install_skills(args):
    result, code = skills.run(args.target, args.remove, args.dry_run, args.yes, project=args.project)
    emit(result)
    return code


def cmd_attach(args):
    return attach.run(args.name, args.wait)


COMMANDS = {"status": cmd_status, "send": cmd_send, "keys": cmd_keys, "wait": cmd_wait, "reply": cmd_reply,
            "where": cmd_where, "read": cmd_read, "ls": cmd_ls, "attach": cmd_attach, "stop": cmd_stop,
            "guide": cmd_guide, "install-skills": cmd_install_skills}


def dispatch(args, agent_command):
    if args.command is None:
        raise CorralError(EXIT_ERROR, "usage", "missing command")
    if args.command == "start" and not agent_command:
        raise CorralError(EXIT_ERROR, "usage", "start needs an agent command after --")
    if hasattr(args, "name"):
        paths.validate_name(args.name)
    if args.command == "start":
        return cmd_start(args, agent_command)
    handler = COMMANDS.get(args.command)
    if handler is None:
        raise CorralError(EXIT_ERROR, "not_implemented", f"{args.command} is not implemented yet")
    return handler(args)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv == ["--version"]:
            emit({"ok": True, "version": __version__, "contract": errors.CONTRACT_VERSION})
            return EXIT_OK
        if not argv or argv[0] not in SANDBOX_EXEMPT:
            sandbox.check()
        own, agent_command = _split_agent_command(argv)
        args = build_parser().parse_args(own)
        return dispatch(args, agent_command)
    except CorralError as e:
        emit(e.to_json())
        return e.exit_code
