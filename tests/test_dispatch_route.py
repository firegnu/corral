"""编排技能的路由脚本：结论怎么给、什么时候交回主控、调不通时不挡路（不联网，全部用假的返回）。"""
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


def answers(tier=(0.0, 1.0, 0.0), tier_conf=0.9, choice="codex", agent_conf=0.9, nouls=(0.1, 0.1, 0.1, 0.1)):
    probs = {"codex": 0.0, "claude": 0.0, "pi": 0.0, "none": 0.0}
    probs[choice] = 1.0
    a = {"tier": {"type": "score", "score": 1.0, "probabilities": {str(i): p for i, p in enumerate(tier)},
                  "confidence": tier_conf},
         "agent": {"type": "choice", "choice": choice, "probabilities": probs, "confidence": agent_conf}}
    for key, v in zip(route.CROSS_REVIEW, nouls):
        a["cross_" + key] = {"type": "noul", "noul": v}
    return {"model": route.MODEL, "answers": a, "usage": {"input_tokens": 700, "output_tokens": 100}}


class VerdictTest(unittest.TestCase):
    def test_confident_answers_become_verdicts(self):
        out = route.shape(answers(tier=(0, 0, 1), tier_conf=0.95, choice="claude", agent_conf=0.99))
        self.assertEqual(out["tier"]["verdict"], "重")
        self.assertEqual(out["agent"]["verdict"], "claude")

    def test_low_confidence_goes_back_to_controller(self):
        out = route.shape(answers(tier_conf=0.79, agent_conf=0.79))
        self.assertIsNone(out["tier"]["verdict"])
        self.assertIsNone(out["agent"]["verdict"])
        self.assertEqual(out["tier"]["level"], "常规")  # 原始判断照样给出，只是不算结论

    def test_none_of_the_agents_is_no_verdict(self):
        self.assertIsNone(route.shape(answers(choice="none", agent_conf=1.0))["agent"]["verdict"])

    def test_chore_agent_only_for_confident_light_tier(self):
        light = (1, 0, 0)
        self.assertEqual(route.shape(answers(tier=light, tier_conf=0.9, choice="pi"))["agent"]["verdict"], "pi")
        self.assertIsNone(route.shape(answers(tier=light, tier_conf=0.78, choice="pi"))["agent"]["verdict"])
        self.assertIsNone(route.shape(answers(tier=(0, 1, 0), tier_conf=0.9, choice="pi"))["agent"]["verdict"])

    def test_cross_review_verdicts(self):
        self.assertEqual(route.shape(answers(nouls=(0.1, 0.85, 0.1, 0.1)))["cross_review"]["verdict"], "要")
        self.assertEqual(route.shape(answers(nouls=(0.2, 0.05, 0.1, 0.0)))["cross_review"]["verdict"], "不要")
        self.assertIsNone(route.shape(answers(nouls=(0.1, 0.5, 0.1, 0.1)))["cross_review"]["verdict"])


class RequestTest(unittest.TestCase):
    def test_default_roster_has_three_agents_and_none(self):
        crit = route.build_request("x", None)["questions"]["agent"]["criteria"]
        self.assertEqual(sorted(crit), ["claude", "codex", "none", "pi"])

    def test_given_agents_replace_the_default_roster(self):
        crit = route.build_request("x", {"codex": "everything"})["questions"]["agent"]["criteria"]
        self.assertEqual(sorted(crit), ["codex", "none"])

    def test_pinned_model(self):
        self.assertEqual(route.build_request("x", None)["model"], "jev-1.13.0")


class FallbackTest(unittest.TestCase):
    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = route.route("x", None)
        self.assertFalse(out["ok"])
        self.assertIn("TYPESAFE_API_KEY", out["error"])

    def test_network_error_retries_once_then_fails_softly(self):
        with mock.patch.object(route.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")) as m:
            out = route.route("x", None, key="k")
        self.assertFalse(out["ok"])
        self.assertEqual(m.call_count, 2)

    def test_bad_response_fails_softly(self):
        with mock.patch.object(route.urllib.request, "urlopen", return_value=io.BytesIO(b'{"model": "x"}')):
            out = route.route("x", None, key="k")
        self.assertFalse(out["ok"])

    def test_good_response(self):
        body = json.dumps(answers()).encode()
        with mock.patch.object(route.urllib.request, "urlopen", return_value=io.BytesIO(body)):
            out = route.route("x", None, key="k")
        self.assertTrue(out["ok"])
        self.assertEqual(out["agent"]["verdict"], "codex")


if __name__ == "__main__":
    unittest.main()
