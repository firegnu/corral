#!/usr/bin/env python3
"""影响面回测：给路由加一道「只影响显示或文档吗」，看它能不能把三档影响面判准。

三档：碰要害 = 现有交叉审查结论「要」；看得见 = 新题 >= YES 且不是碰要害；其余 = 改行为。
新题有几种措辞，每种对每条摘要问一次（和现有 5 道题同一个请求，和真实用法一致）。
用法：impact_eval.py <cases.json>
cases.json：[[id, 人标的档, 摘要], ...]。案例数据含具体项目内容，放在仓库外。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../corral-dispatch-skill"))
import route  # noqa: E402

YES = 0.8

VARIANTS = {
    "C": "Is the task in `task_summary` limited to presentation (visual layout, colors, text, documentation) or "
         "read-only research, with no new features, no input handling changes and no logic changes?",
    "F": "Does the task in `task_summary` only produce a written proposal, research notes or a design document, "
         "without changing any program code?",
}


def ask(summary, key):
    payload = route.build_request(summary)
    for name, text in VARIANTS.items():
        payload["questions"]["visible_" + name] = {"type": "noul", "instructions": text}
    resp = route.call(payload, key)
    base = route.shape(resp)
    probs = {name: round(resp["answers"]["visible_" + name]["noul"], 2) for name in VARIANTS}
    return base, probs


def classify(cross_verdict, p):
    if cross_verdict == "要":
        return "碰要害"
    if p >= YES:
        return "看得见"
    return "改行为"


def main(path):
    key = os.environ["TYPESAFE_API_KEY"]
    cases = json.load(open(path, encoding="utf-8"))
    hits = {n: 0 for n in VARIANTS}
    rows = []
    for cid, want, summary in cases:
        base, probs = ask(summary, key)
        cv = base["cross_review"]["verdict"]
        got = {n: classify(cv, p) for n, p in probs.items()}
        for n in VARIANTS:
            hits[n] += got[n] == want
        marks = "  ".join(f"{n}={probs[n]:.2f}{'' if got[n] == want else '✗' + got[n]}" for n in VARIANTS)
        print(f"{cid:<20} 应{want}  交叉={cv or '拿不准'}  {marks}", flush=True)
        rows.append({"id": cid, "want": want, "cross": cv, "probs": probs, "got": got})
    print()
    print("  ".join(f"{n}: {hits[n]}/{len(cases)}" for n in VARIANTS))
    json.dump(rows, open(path.replace(".json", ".out.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
