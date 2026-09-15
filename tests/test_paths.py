import os
import unittest
from unittest import mock

from tests import support  # noqa: F401  （把 src 加进路径）
from corral import errors, paths


class NameTest(unittest.TestCase):
    def test_valid_names(self):
        for name in ["a", "demo/alice", "demo/ask-3", "x.y_z", "A1/b2/c3"]:
            with self.subTest(name=name):
                paths.validate_name(name)

    def test_invalid_names(self):
        bad = ["", "/abs", "a//b", "a/", "../x", "a/../b", ".", "..", "has space", "中文",
               "-dash", ".hidden", "a/.b", "a\nb"]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(errors.CorralError) as cm:
                    paths.validate_name(name)
                self.assertEqual(cm.exception.exit_code, errors.EXIT_ERROR)

    def test_segment_cannot_be_internal_file_name(self):
        # 名字就是目录路径；段名不能和栏位目录里的内部文件重名
        for name in ["demo/lock", "demo/sock", "events", "a/meta.json", "a/hook.py", "a/cursor",
                     "a/exit.json", "a/pen.log"]:
            with self.subTest(name=name):
                with self.assertRaises(errors.CorralError):
                    paths.validate_name(name)


class StateHomeTest(unittest.TestCase):
    def test_default_is_home_dot_corral(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORRAL_HOME", None)
            self.assertEqual(paths.state_home(), os.path.join(os.path.expanduser("~"), ".corral"))

    def test_env_overrides(self):
        with mock.patch.dict(os.environ, {"CORRAL_HOME": "/tmp/somewhere"}):
            self.assertEqual(paths.state_home(), "/tmp/somewhere")

    def test_pen_dir_is_name_path(self):
        with mock.patch.dict(os.environ, {"CORRAL_HOME": "/tmp/h"}):
            self.assertEqual(paths.pen_dir("demo/alice"), "/tmp/h/demo/alice")


class SockPathTest(unittest.TestCase):
    def test_short_path_ok(self):
        with mock.patch.dict(os.environ, {"CORRAL_HOME": "/tmp/h"}):
            self.assertEqual(paths.sock_path("demo/alice"), "/tmp/h/demo/alice/sock")

    def test_too_long_rejected(self):
        home = "/tmp/" + "d" * 90
        with mock.patch.dict(os.environ, {"CORRAL_HOME": home}):
            with self.assertRaises(errors.CorralError) as cm:
                paths.sock_path("demo/alice")
            self.assertEqual(cm.exception.exit_code, errors.EXIT_ERROR)
            self.assertIn("socket", cm.exception.message)

    def test_limit_matches_platform(self):
        self.assertEqual(paths.SOCK_PATH_MAX, 104 if os.uname().sysname == "Darwin" else 108)


if __name__ == "__main__":
    unittest.main()
