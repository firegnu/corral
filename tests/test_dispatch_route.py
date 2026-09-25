"""编排技能的路由脚本：只问几档、要不要交叉审查和影响面；结论怎么给、什么时候交回主控、调不通时不挡路（不联网，全部用假的返回）。"""
import importlib.util
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from tests import support

PATH = os.path.join(os.path.dirname(support.SRC), "corral-dispatch-skill", "route.py")
spec = importlib.util.spec_from_file_location("dispatch_route", PATH)
assert spec and spec.loader
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


def answers(tier=(0.0, 1.0, 0.0), tier_conf=0.9, nouls=(0.1, 0.1, 0.1, 0.1), visible=0.1, doc_only=0.02):
    a = {"tier": {"type": "score", "score": 1.0, "probabilities": {str(i): p for i, p in enumerate(tier)},
                  "confidence": tier_conf}}
    for key, v in zip(route.CROSS_REVIEW, nouls):
        a["cross_" + key] = {"type": "noul", "noul": v}
    a["visible"] = {"type": "noul", "noul": visible}
    a["doc_only"] = {"type": "noul", "noul": doc_only}
    return {"model": route.MODEL, "answers": a, "usage": {"input_tokens": 700, "output_tokens": 100}}


class VerdictTest(unittest.TestCase):
    def test_confident_tier_becomes_verdict(self):
        self.assertEqual(route.shape(answers(tier=(0, 0, 1), tier_conf=0.95))["tier"]["verdict"], "重")

    def test_low_confidence_goes_back_to_controller(self):
        out = route.shape(answers(tier_conf=0.79))
        self.assertIsNone(out["tier"]["verdict"])
        self.assertEqual(out["tier"]["level"], "常规")  # 原始判断照样给出，只是不算结论

    def test_no_agent_choice_in_output(self):
        self.assertNotIn("agent", route.shape(answers()))

    def test_cross_review_verdicts(self):
        self.assertEqual(route.shape(answers(nouls=(0.1, 0.85, 0.1, 0.1)))["cross_review"]["verdict"], "要")
        self.assertEqual(route.shape(answers(nouls=(0.2, 0.05, 0.1, 0.0)))["cross_review"]["verdict"], "不要")
        self.assertIsNone(route.shape(answers(nouls=(0.1, 0.5, 0.1, 0.1)))["cross_review"]["verdict"])

    def test_impact_verdicts(self):
        def impact(**kw):
            return route.shape(answers(**kw))["impact"]["verdict"]
        self.assertEqual(impact(nouls=(0.1, 0.9, 0.1, 0.1), visible=0.9), "碰要害")  # 碰要害压过看得见
        self.assertEqual(impact(visible=0.85), "看得见")
        self.assertEqual(impact(visible=0.1), "改行为")  # 不只影响显示，也不碰要害
        self.assertIsNone(impact(visible=0.5))  # 新题拿不准
        self.assertIsNone(impact(nouls=(0.1, 0.5, 0.1, 0.1), visible=0.1))  # 交叉审查拿不准
        self.assertEqual(impact(visible=0.5, doc_only=0.95), "看得见")  # 只写方案、不改代码
        self.assertIsNone(impact(visible=0.1, doc_only=0.5))  # 两题取较高的那个
        self.assertEqual(route.shape(answers(visible=0.85))["impact"]["visible"], 0.85)


class RequestTest(unittest.TestCase):
    def test_asks_only_tier_cross_review_and_visible(self):
        questions = route.build_request("x")["questions"]
        self.assertEqual(sorted(questions),
                         sorted(["tier", "visible", "doc_only"] + ["cross_" + k for k in route.CROSS_REVIEW]))

    def test_pinned_model(self):
        self.assertEqual(route.build_request("x")["model"], "jev-1.13.0")


class FallbackTest(unittest.TestCase):
    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = route.route("x")
        self.assertFalse(out["ok"])
        self.assertIn("TYPESAFE_API_KEY", out["error"])

    def test_network_error_retries_once_then_fails_softly(self):
        with mock.patch.object(route.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")) as m:
            out = route.route("x", key="k")
        self.assertFalse(out["ok"])
        self.assertEqual(m.call_count, 2)

    def test_bad_response_fails_softly(self):
        with mock.patch.object(route.urllib.request, "urlopen", return_value=io.BytesIO(b'{"model": "x"}')):
            out = route.route("x", key="k")
        self.assertFalse(out["ok"])

    def test_good_response(self):
        body = json.dumps(answers()).encode()
        with mock.patch.object(route.urllib.request, "urlopen", return_value=io.BytesIO(body)):
            out = route.route("x", key="k")
        self.assertTrue(out["ok"])
        self.assertEqual(out["tier"]["verdict"], "常规")


if __name__ == "__main__":
    unittest.main()
