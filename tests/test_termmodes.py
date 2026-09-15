"""M3：终端模式记录 / 重放 / 还原、退出键解析、人工按键分类（纯函数单元测试）。"""
import unittest

from tests import support  # noqa: F401
from corral import termmodes as tm


class ModeTrackingTest(unittest.TestCase):
    def feed_all(self, *chunks):
        st = tm.TerminalState()
        for c in chunks:
            st.feed(c)
        return st

    def test_tracks_dec_private_modes(self):
        st = self.feed_all(b"\x1b[?2004h\x1b[?1004h\x1b[?1h", b"\x1b[?1000;1006h\x1b[?25l")
        self.assertEqual(st.modes, {"2004": "h", "1004": "h", "1": "h", "1000": "h", "1006": "h", "25": "l"})
        st.feed(b"\x1b[?1004l")
        self.assertEqual(st.modes["1004"], "l")

    def test_sequence_split_across_chunks(self):
        st = self.feed_all(b"xx\x1b[?20", b"04hyy\x1b]0;ti", b"tle\x07")
        self.assertEqual(st.modes.get("2004"), "h")
        self.assertEqual(st.title, "title")

    def test_same_sequence_not_double_counted_across_chunks(self):
        # 尾巴会拼到下一块再扫一遍；push 不能因此算两次
        st = self.feed_all(b"\x1b[>1u", b"plain output")
        self.assertEqual(st.kitty, [1])

    def test_kitty_stack(self):
        st = self.feed_all(b"\x1b[>1u\x1b[>7u")
        self.assertEqual(st.kitty, [1, 7])
        st.feed(b"\x1b[=3u")
        self.assertEqual(st.kitty, [1, 3])
        st.feed(b"\x1b[<u")
        self.assertEqual(st.kitty, [1])
        st.feed(b"\x1b[<5u")
        self.assertEqual(st.kitty, [])

    def test_keypad_and_modify_other_keys(self):
        st = self.feed_all(b"\x1b=\x1b[>4;2m")
        self.assertTrue(st.keypad_app)
        self.assertEqual(st.modify_other_keys, 2)
        st.feed(b"\x1b>\x1b[>4m")
        self.assertFalse(st.keypad_app)
        self.assertEqual(st.modify_other_keys, 0)

    def test_replay_puts_alt_screen_first(self):
        st = self.feed_all(b"\x1b[?2004h\x1b[?1049h\x1b[>7u\x1b=")
        seq = st.replay()
        self.assertTrue(seq.startswith(b"\x1b[?1049h"))
        for part in (b"\x1b[?2004h", b"\x1b[>7u", b"\x1b="):
            self.assertIn(part, seq)

    def test_restore_undoes_what_was_set(self):
        st = self.feed_all(b"\x1b[?2004h\x1b[?1049h\x1b[>7u\x1b[?25l\x1b[?1h\x1b=\x1b[>4;2m")
        seq = st.restore()
        for part in (b"\x1b[?2004l", b"\x1b[?1l", b"\x1b[<1u", b"\x1b[?25h", b"\x1b>", b"\x1b[>4m"):
            self.assertIn(part, seq)
        self.assertTrue(seq.endswith(b"\x1b[?1049l"))
        self.assertNotIn(b"\x1b[?1004l", seq)  # 没开过的不动

    def test_sync_output_mode_not_replayed(self):
        st = self.feed_all(b"\x1b[?2026h")
        self.assertNotIn(b"2026", st.replay())


class DetachKeyTest(unittest.TestCase):
    def test_detach_encodings(self):
        for data in (b"\x1d", b"ab\x1d", b"\x1b[93;5u", b"\x1b[93;5:1u", b"\x1b[93:125;5u",
                     b"\x1b[93;69u", b"\x1b[27;5;93~"):
            with self.subTest(data=data):
                self.assertIsNotNone(tm.find_detach(data))

    def test_not_detach(self):
        for data in (b"a", b"\x1b[93;6u", b"\x1b[97;5u", b"\x1b[93;5:3u", b"\x1b[93u", b"]"):
            with self.subTest(data=data):
                self.assertIsNone(tm.find_detach(data))

    def test_position_is_start_of_key(self):
        self.assertEqual(tm.find_detach(b"abc\x1b[93;5u"), 3)


class HumanInputTest(unittest.TestCase):
    def test_terminal_auto_responses_are_not_human(self):
        for data in (b"\x1b[I", b"\x1b[O", b"\x1b[12;40R", b"\x1b[?62;22c", b"\x1b[>1;10;0c",
                     b"\x1b[0n", b"\x1b]11;rgb:1e1e/1e1e/1e1e\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x07",
                     b"\x1bP>|ghostty 1.3.1\x1b\\", b"\x1b[?2004;1$y", b"\x1b[?7u", b"\x1b[8;40;120t",
                     b"\x1b[I\x1b[12;40R"):
            with self.subTest(data=data):
                self.assertFalse(tm.is_human_input(data))

    def test_mouse_motion_without_buttons_is_not_human(self):
        # M8 实测：agent 打开 1003 鼠标上报后，鼠标只是划过窗口，终端就持续发 ESC[<35;x;yM
        for data in (b"\x1b[<35;10;45M", b"\x1b[<39;1;1M", b"\x1b[<35;10;45M\x1b[<35;11;45M\x1b[I"):
            with self.subTest(data=data):
                self.assertFalse(tm.is_human_input(data))

    def test_mouse_buttons_drag_and_wheel_are_human(self):
        for data in (b"\x1b[<0;10;5M", b"\x1b[<0;10;5m", b"\x1b[<32;10;5M", b"\x1b[<64;10;5M", b"\x1b[<65;10;5M",
                     b"\x1b[<35;10;45Ma"):
            with self.subTest(data=data):
                self.assertTrue(tm.is_human_input(data))

    def test_keys_and_mouse_are_human(self):
        for data in (b"a", b"\r", b"\x1b", b"\x1b[A", b"\x1b[97;5u", b"\x1b[<0;10;5M", b"\x1b[I" + b"x",
                     "中".encode()):
            with self.subTest(data=data):
                self.assertTrue(tm.is_human_input(data))


if __name__ == "__main__":
    unittest.main()
