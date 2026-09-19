#!/usr/bin/env python3
"""路由小试验：对每条任务摘要（中文、英文各一份）调一次 route，和人标的答案对照。

用法：eval.py <cases.json> <结果.json>
cases.json：{"agents": {名字: 范围}（可省，省了用默认分工）, "cases": [{"id", "zh", "en", "label": {"tier", "agent", "cross": [...]}}]}
每一项按 route 给的结论分三类：一致、拿不准（交回主控）、不一致（会被采用的错判）。
案例数据可能含具体项目的内容，放在仓库外。
"""
import json
import sys

from route import route


def main(cases_path, out_path):
    data = json.load(open(cases_path, encoding="utf-8"))
    results, tally = [], {}
    for case in data["cases"]:
        label = case["label"]
        want = {"tier": label["tier"], "agent": label["agent"], "cross": "要" if label["cross"] else "不要"}
        row = {"id": case["id"], "label": label}
        for lang in ("zh", "en"):
            r = route(case[lang], data.get("agents"))
            row[lang] = r
            if not r["ok"]:
                print(f"{case['id']} {lang}: {r['error']}")
                continue
            got = {"tier": r["tier"]["verdict"], "agent": r["agent"]["verdict"], "cross": r["cross_review"]["verdict"]}
            marks = {}
            for k in got:
                kind = "拿不准" if got[k] is None else "一致" if got[k] == want[k] else "错判"
                tally.setdefault((lang, k), {}).setdefault(kind, 0)
                tally[(lang, k)][kind] += 1
                marks[k] = "" if kind == "一致" else "?" if kind == "拿不准" else f"✗应{want[k]}"
            c = r["cross_review"]
            print(f"{case['id']:<12} {lang}  档 {r['tier']['level']}({r['tier']['confidence']:.2f}){marks['tier']:<6}"
                  f" 家 {r['agent']['choice']}({r['agent']['confidence']:.2f}){marks['agent']:<9}"
                  f" 审 {c['verdict'] or '-'}{marks['cross']}  "
                  + " ".join(f"{k[:4]}={c[k]:.2f}" for k in c if k != "verdict"))
        results.append(row)
    print()
    for (lang, k), v in sorted(tally.items()):
        print(f"{lang} {k}: " + "  ".join(f"{kind} {n}" for kind, n in sorted(v.items())))
    json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
