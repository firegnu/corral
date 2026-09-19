#!/usr/bin/env python3
"""编排技能的路由：把一段任务摘要交给 TypeSafe 的分类模型，一次请求问完「几档、要不要交叉审查」。
交给哪家不问它，由主控照 SKILL.md 第 3 节的分工表定。

主控拆完每件活调一次，用法见 SKILL.md 第 3 节。只用标准库；key 从环境变量 TYPESAFE_API_KEY 读。
用法：echo "<任务摘要>" | route.py
输出一行 JSON，每一项带 verdict（null 表示拿不准，由主控自己判断）；调不通时 {"ok": false, "error": ...}，退出码 1，
主控照没有路由时的做法判断，不挡流程。试验记录见 docs/SPIKE.md「路由试验」。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # 固定版本，别用 jev-latest：别名会随新版本漂移
TIMEOUT = 10
RETRY_STATUS = {429, 529}

# ---- 题目和门槛都在这里，改这一处 ----
TIERS = ["轻", "常规", "重"]
TIER_QUESTION = {
    "type": "score",
    "instructions": "How much reasoning capacity does a coding agent need to do the task in `task_summary` well?",
    "criteria": [
        "Light: looking things up, mechanical edits, editing text or data by an explicit checklist, "
        "or running commands and reporting results. Little design judgment.",
        "Regular: implementing a feature or fixing an ordinary bug by following a clear task description. "
        "Normal design judgment within one module.",
        "Heavy: designing a data model, concurrency or transactions, cross-module design, hard-to-find bugs, "
        "or careful review of someone else's code. Mistakes are costly or subtle.",
    ],
}
CROSS_REVIEW = {
    "data_model": "Does the task in `task_summary` create or change database table structure, such as adding or "
                  "altering tables, columns, constraints or migrations? Only reading or writing rows does not count.",
    "concurrency": "Does the task in `task_summary` involve concurrency, background workers, locking, retries, "
                   "or database transactions?",
    "security_privacy": "Does the task in `task_summary` handle security or private data, such as permissions, "
                        "file system isolation, logging of personal content, or exposing data to others?",
    "core_rules": "Does the task in `task_summary` implement or change the product's core business rules, "
                  "the central decision logic the product depends on?",
}
CONFIDENT = 0.8   # 几档：置信度到这个数才给结论，否则交回主控
CROSS_YES = 0.8   # 交叉审查：任何一题到这个数算「要」
CROSS_NO = 0.2    # 四题都不超过这个数算「不要」；其余交回主控


def build_request(summary):
    questions = {"tier": TIER_QUESTION}
    for key, text in CROSS_REVIEW.items():
        questions["cross_" + key] = {"type": "noul", "instructions": text}
    return {"state": {"task_summary": summary}, "model": MODEL, "questions": questions}


def call(payload, key):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in (1, 2):  # 最多重试一次
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUS and attempt == 1:
                time.sleep(1)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 1:
                continue
            raise RuntimeError(f"network: {e}")


def shape(resp):
    a = resp["answers"]
    tier = a["tier"]
    probs = {TIERS[int(k)]: round(v, 3) for k, v in tier["probabilities"].items()}
    level = max(probs, key=lambda k: probs[k])
    # verdict 是给主控的结论；None 表示拿不准，用主控自己的判断
    tier_verdict = level if tier["confidence"] >= CONFIDENT else None
    out = {"ok": True, "model": resp.get("model"),
           "tier": {"verdict": tier_verdict, "level": level, "score": round(tier["score"], 3),
                    "probabilities": probs, "confidence": round(tier["confidence"], 3)}}
    cross = {k[len("cross_"):]: round(v["noul"], 3) for k, v in a.items() if k.startswith("cross_")}
    top = max(cross.values())
    out["cross_review"] = {"verdict": "要" if top >= CROSS_YES else "不要" if top <= CROSS_NO else None, **cross}
    out["usage"] = resp.get("usage")
    return out


def route(summary, key=None):
    key = key or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {"ok": False, "error": "TYPESAFE_API_KEY is not set"}
    try:
        return shape(call(build_request(summary), key))
    except Exception as e:  # 任何意外都退回主控自己判断，不挡分派
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    argparse.ArgumentParser(description="从标准输入读任务摘要，输出一行 JSON").parse_args()
    summary = sys.stdin.read().strip()
    result = route(summary)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
