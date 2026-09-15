"""corral install-skills：经人确认后，把 agent skill 写进 Claude Code 和 Codex 的 skill 目录。

全局：Claude Code ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/corral/SKILL.md，Codex ~/.agents/skills/corral/SKILL.md（官方文档位置）；
项目级（--project）：<目录>/.claude/skills/ 和 <目录>/.agents/skills/。
只写这两个文件；删除时只删带 corral 标记的文件。默认不安装（DESIGN 13.3、14）。
"""
import os
import shutil
import sys

from corral.errors import EXIT_ERROR, EXIT_OK, CorralError

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill", "SKILL.md")
MARKER = "<!-- corral-skill:"
AGENTS = ("claude", "codex")


def skill_path(agent, project=None):
    if project is not None:
        sub = ".claude" if agent == "claude" else ".agents"
        return os.path.join(project, sub, "skills", "corral", "SKILL.md")
    if agent == "claude":
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    else:
        # Codex 官方文档的用户级位置；实测 0.154 也读 ~/.codex/skills，只装文档位置这一处
        base = os.path.join(os.path.expanduser("~"), ".agents")
    return os.path.join(base, "skills", "corral", "SKILL.md")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def plan(target, remove, project=None):
    agents = AGENTS if target == "all" else (target,)
    source = _read(SOURCE)
    items = []
    for agent in agents:
        path = skill_path(agent, project)
        current = _read(path)
        if remove:
            status = "absent" if current is None else ("remove" if MARKER in current else "foreign")
        else:
            status = "create" if current is None else ("same" if current == source else "overwrite")
        items.append({"agent": agent, "path": path, "status": status})
    return items, source


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


def _remove(path):
    os.unlink(path)
    try:
        os.rmdir(os.path.dirname(path))  # 只删空的 corral 目录，不动 skills 目录
    except OSError:
        pass


def _confirm(items, remove):
    verb = "删除" if remove else "写入"
    sys.stderr.write(f"corral install-skills 将{verb}：\n")
    for i in items:
        sys.stderr.write(f"  [{i['status']}] {i['path']}\n")
    sys.stderr.write(f"确认{verb}以上文件？[y/N] ")
    sys.stderr.flush()
    answer = sys.stdin.readline().strip().lower()
    return answer in ("y", "yes")


def run(target, remove, dry_run, yes, project=None):
    if project is not None:
        project = os.path.abspath(project)
        if not os.path.isdir(project):
            raise CorralError(EXIT_ERROR, "bad_project", f"no such directory: {project}")
    items, source = plan(target, remove, project)
    warnings = []
    if not remove and shutil.which("corral") is None:
        warnings.append("corral is not on PATH: the skill will tell agents that corral is not installed")
    for i in items:
        if i["status"] == "foreign":
            warnings.append(f"{i['path']} was not written by corral; left in place")
    action = "remove" if remove else "install"
    todo = [i for i in items if i["status"] in ("create", "overwrite", "remove")]
    result = {"ok": True, "action": action, "dry_run": dry_run, "written": False, "items": items,
              "warnings": warnings}
    if dry_run or not todo:
        return result, EXIT_OK
    if not yes:
        if not sys.stdin.isatty():
            raise CorralError(EXIT_ERROR, "confirmation_required",
                              "install-skills writes to global skill directories; run it in a terminal to confirm, "
                              "or pass --yes after the user agreed", action=action, items=items, warnings=warnings)
        if not _confirm(todo, remove):
            raise CorralError(EXIT_ERROR, "declined", "nothing written", action=action, items=items,
                              warnings=warnings)
    for i in todo:
        if remove:
            _remove(i["path"])
        else:
            _write(i["path"], source)
    result["written"] = True
    return result, EXIT_OK
