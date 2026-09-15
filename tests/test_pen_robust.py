"""M8 发现：栏位处理接入窗口时不能崩（栏位一崩，agent 被一起杀掉）。"""
import os
import selectors
import signal
import socket
import time
import unittest

from tests import support
from tests.test_agent_state import AgentTestCase
from tests.test_attach import Window
from corral import pen


def bare_pen():
    d = support.short_tmpdir()
    return pen.Pen({"dir": d, "name": "demo/x", "instance": "0123456789ab", "cwd": d, "argv": ["true"],
                    "kind": "true", "env": {}, "version": "test"})


def connected_client(p):
    mine, peer = socket.socketpair()
    mine.setblocking(False)
    c = pen.Client(mine)
    p.clients[mine] = c
    p.sel.register(mine, selectors.EVENT_READ, c)
    return c, peer


class ClientEventTest(unittest.TestCase):
    def test_read_event_after_same_client_dropped_by_write(self):
        # kqueue 会把同一个 socket 的可写、可读拆成两条事件返回：先写失败断开，再处理可读
        p = bare_pen()
        c, peer = connected_client(p)
        peer.close()
        c.out += b"x" * 4096
        p.on_client(c, selectors.EVENT_WRITE)
        self.assertNotIn(c, p.clients.values())
        p.on_client(c, selectors.EVENT_READ)   # 不能抛 EBADF

    def test_unexpected_error_in_one_client_only_drops_that_client(self):
        p = bare_pen()
        c1, peer1 = connected_client(p)
        c2, peer2 = connected_client(p)

        def boom(c, req):
            raise RuntimeError("unexpected")
        p.handle_request = boom
        peer1.sendall(b'{"proto": 1, "op": "status"}\n')
        time.sleep(0.05)
        p.safe_on_client(c1, selectors.EVENT_READ)
        self.assertNotIn(c1, p.clients.values())
        self.assertIn(c2, p.clients.values())
        peer1.close()
        peer2.close()


class KillWindowWhileOutputFlowsTest(AgentTestCase):
    def test_pen_and_agent_survive_repeated_window_kills(self):
        self.start("demo/c", "claude", script=["slow:60"])   # 一直转圈输出
        self.states_until("demo/c", lambda s: s.get("state") == "working")
        meta = support.read_meta(self.home, "demo/c")
        for round_ in range(6):
            with self.subTest(round=round_):
                w = Window(self.home, "demo/c", rows=30 + round_, cols=100)
                self.assertTrue(support.wait_until(lambda: self.status("demo/c").get("attached") == 1))
                time.sleep(0.3)
                os.kill(w.pid, signal.SIGKILL)
                os.waitpid(w.pid, 0)
                os.close(w.fd)
                w.exit_code = -9
                self.assertTrue(support.wait_until(lambda: self.status("demo/c").get("attached") == 0))
                os.kill(meta["pen_pid"], 0)
                os.kill(meta["agent_pid"], 0)
        self.assertEqual(self.status("demo/c")["state"], "working")


if __name__ == "__main__":
    unittest.main()
