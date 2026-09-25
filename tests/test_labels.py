"""start --label：按实例保存，status / wait / ls 原样返回；corral 不解读。"""
import json
import os
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase


class LabelTest(AgentTestCase):
    def start_labeled(self, name, *labels):
        args = ["start", name, "--cwd", self.home]
        for label in labels:
            args += ["--label", label]
        return self.cli(*args, "--", support.fake_agent("claude"))

    def test_labels_returned_by_status_wait_and_ls(self):
        code, out = self.start_labeled("demo/l", "effort=high", "model=opus", "effort=xhigh")
        self.assertEqual(code, 0, out)
        want = {"effort": "xhigh", "model": "opus"}  # 同一个键以最后一次为准
        self.assertEqual(self.status("demo/l")["labels"], want)
        self.assertEqual(self.cli("wait", "demo/l")[1]["labels"], want)
        entry = next(a for a in self.cli("ls")[1]["agents"] if a["name"] == "demo/l")
        self.assertEqual(entry["labels"], want)

    def test_no_labels_is_empty_object(self):
        self.assertEqual(self.start_labeled("demo/n")[0], 0)
        self.assertEqual(self.status("demo/n")["labels"], {})
        entry = next(a for a in self.cli("ls")[1]["agents"] if a["name"] == "demo/n")
        self.assertEqual(entry["labels"], {})

    def test_value_is_kept_verbatim(self):
        self.assertEqual(self.start_labeled("demo/v", "note=a=b c，中文")[0], 0)
        self.assertEqual(self.status("demo/v")["labels"], {"note": "a=b c，中文"})

    def test_bad_labels_are_usage_errors(self):
        for bad in ("noequals", "=x", "-lead=x", "has space=x"):
            with self.subTest(label=bad):
                code, out = self.start_labeled("demo/bad", bad)
                self.assertEqual(code, 1, out)
                self.assertEqual(out["error"], "usage")
                self.assertEqual(self.cli("status", "demo/bad")[0], 2)  # 没拉起来

    def test_restart_does_not_inherit_old_labels(self):
        self.assertEqual(self.start_labeled("demo/r", "effort=high")[0], 0)
        self.assertEqual(self.cli("stop", "demo/r")[0], 0)
        self.assertEqual(self.start_labeled("demo/r")[0], 0)
        self.assertEqual(self.status("demo/r")["labels"], {})

    def test_labels_of_another_instance_are_ignored(self):
        self.assertEqual(self.start_labeled("demo/s", "effort=high")[0], 0)
        path = os.path.join(self.home, "demo", "s", "labels.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        saved["instance"] = "0" * 12
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        self.assertEqual(self.status("demo/s")["labels"], {})


if __name__ == "__main__":
    unittest.main()
