"""终端层面的通用信息（不是任何 agent 的界面内容）：

- 从 agent 输出里记下终端标题、终端模式（DEC 私有模式、应用键盘、modifyOtherKeys、kitty 键盘协议），
  新窗口接入时重放、退出接入时还原；
- 解析退出接入键（Ctrl-]，含 kitty 键盘协议和 modifyOtherKeys 编码）；
- 区分人工按键和终端自动发回的应答；
- 给 `corral read` 去掉控制字符。
"""
import re

CSI_RE = re.compile(rb"\x1b\[([0-?]*)([ -/]*)([@-~])")
OSC_RE = re.compile(rb"\x1b\]([^\x07\x1b]*)(?:\x07|\x1b\\)")
ESC2_RE = re.compile(rb"\x1b([=>])")
INCOMPLETE_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*\x1b?)?\Z")
MAX_PENDING = 4096

# 记录并重放的 DEC 私有模式及其终端默认值
MODE_DEFAULTS = {
    "1": "l",      # 应用光标键
    "25": "h",     # 光标可见
    "1000": "l", "1002": "l", "1003": "l", "1005": "l", "1006": "l", "1015": "l",  # 鼠标
    "1004": "l",   # 焦点上报
    "2004": "l",   # 粘贴模式
    "47": "l", "1047": "l", "1049": "l",  # 备用屏幕
}
ALT_SCREEN = ("1049", "1047", "47")


class TerminalState:
    """增量读 agent 输出。转义序列可能被切在两块之间：只把没收完整的那一段留到下一块。"""

    def __init__(self):
        self.title = ""
        self.modes = {}
        self.kitty = []
        self.keypad_app = False
        self.modify_other_keys = 0
        self._pending = b""

    def feed(self, data):
        buf = self._pending + data
        self._pending = b""
        i = 0
        while True:
            j = buf.find(b"\x1b", i)
            if j < 0:
                return
            m = CSI_RE.match(buf, j) or OSC_RE.match(buf, j) or ESC2_RE.match(buf, j)
            if m is None:
                if len(buf) - j <= MAX_PENDING and INCOMPLETE_RE.match(buf, j):
                    self._pending = buf[j:]
                    return
                i = j + 1
                continue
            self._apply(m)
            i = m.end()

    def _apply(self, m):
        if m.re is OSC_RE:
            code, _, text = m.group(1).partition(b";")
            if code in (b"0", b"2"):
                self.title = text.decode("utf-8", "replace")
        elif m.re is ESC2_RE:
            self.keypad_app = m.group(1) == b"="
        else:
            params, final = m.group(1), m.group(3)
            if params.startswith(b"?") and final in (b"h", b"l"):
                for num in params[1:].decode().split(";"):
                    if num in MODE_DEFAULTS:
                        self.modes[num] = final.decode()
            elif final == b"u" and params[:1] in (b">", b"<", b"="):
                self._kitty(params[:1], [int(x) if x else 0 for x in params[1:].decode().split(";")])
            elif final == b"m" and params.startswith(b">"):
                nums = [int(x) if x else 0 for x in params[1:].decode().split(";")]
                if nums and nums[0] == 4:
                    self.modify_other_keys = nums[1] if len(nums) > 1 else 0

    def _kitty(self, kind, nums):
        if kind == b">":
            self.kitty.append(nums[0])
        elif kind == b"<":
            n = nums[0] or 1
            del self.kitty[max(0, len(self.kitty) - n):]
        else:
            flags, mode = nums[0], (nums[1] if len(nums) > 1 and nums[1] else 1)
            current = self.kitty.pop() if self.kitty else 0
            self.kitty.append(flags if mode == 1 else (current | flags if mode == 2 else current & ~flags))

    def _changed_modes(self):
        return {k: v for k, v in self.modes.items() if v != MODE_DEFAULTS[k]}

    def replay(self):
        """新窗口接入时先发给它：先切备用屏幕，再开其他模式。"""
        changed = self._changed_modes()
        seq = b"".join(b"\x1b[?" + k.encode() + b"h" for k in ALT_SCREEN if changed.get(k) == "h")
        for k, v in sorted(changed.items()):
            if k not in ALT_SCREEN:
                seq += b"\x1b[?" + k.encode() + v.encode()
        if self.keypad_app:
            seq += b"\x1b="
        if self.modify_other_keys:
            seq += b"\x1b[>4;" + str(self.modify_other_keys).encode() + b"m"
        for flags in self.kitty:
            seq += b"\x1b[>" + str(flags).encode() + b"u"
        return seq

    def restore(self):
        """退出接入时发给窗口：把改过的模式还原，最后离开备用屏幕。"""
        changed = self._changed_modes()
        seq = b""
        for k in sorted(changed):
            if k not in ALT_SCREEN:
                seq += b"\x1b[?" + k.encode() + MODE_DEFAULTS[k].encode()
        if self.kitty:
            seq += b"\x1b[<" + str(len(self.kitty)).encode() + b"u"
        if self.keypad_app:
            seq += b"\x1b>"
        if self.modify_other_keys:
            seq += b"\x1b[>4m"
        seq += b"".join(b"\x1b[?" + k.encode() + b"l" for k in ALT_SCREEN if changed.get(k) == "h")
        return seq


# ---- 退出接入键

DETACH_BYTE = b"\x1d"  # Ctrl-]
DETACH_CSI_U_RE = re.compile(rb"\x1b\[93(?::[\d:]*)?;(\d+)(?::(\d+))?u")
DETACH_MOK_RE = re.compile(rb"\x1b\[27;(\d+);93~")
LOCK_BITS = 64 | 128  # Caps Lock、Num Lock 不影响判断


def _ctrl_only(modifier):
    bits = int(modifier) - 1
    return bits & ~LOCK_BITS == 4


def find_detach(data):
    """返回退出接入键在 data 里的起始位置；没有返回 None。只认按下（不认松开、重复）。"""
    hits = []
    i = data.find(DETACH_BYTE)
    if i >= 0:
        hits.append(i)
    for m in DETACH_CSI_U_RE.finditer(data):
        if _ctrl_only(m.group(1)) and m.group(2) in (None, b"1"):
            hits.append(m.start())
    for m in DETACH_MOK_RE.finditer(data):
        if _ctrl_only(m.group(1)):
            hits.append(m.start())
    return min(hits) if hits else None


# ---- 人工按键

AUTO_RESPONSE_RE = re.compile(
    rb"\x1b\[[IO]"                        # 焦点切入 / 切出
    rb"|\x1b\[\d+;\d+R"                   # 光标位置报告
    rb"|\x1b\[[?>=][\d;]*c"               # 设备属性
    rb"|\x1b\[\d*n"                       # 设备状态
    rb"|\x1b\[\?[\d;]*\$y"                # 模式查询应答
    rb"|\x1b\[\?\d*u"                     # kitty 键盘协议查询应答
    rb"|\x1b\[\d+;\d+;\d+t"               # 窗口尺寸报告
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC 应答（颜色等）
    rb"|\x1bP[^\x1b]*\x1b\\"              # DCS 应答（终端版本等）
)


def is_human_input(data):
    """去掉终端自动发回的应答后还有东西，就算人工操作（按键、粘贴、鼠标）。"""
    return bool(AUTO_RESPONSE_RE.sub(b"", data))


# ---- 排查用

CONTROL_SEQ_RE = re.compile(
    rb"\x1b\[[0-?]*[ -/]*[@-~]"
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b[P^_][^\x1b]*\x1b\\"
    rb"|\x1b[@-Z\\-_=>]"
)
C0_RE = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]")


def strip_controls(data):
    """去掉控制序列，换行统一成 \\n。没有终端模拟，agent 重绘时文字会乱，只作排查用。"""
    data = CONTROL_SEQ_RE.sub(b"", data).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return C0_RE.sub(b"", data).decode("utf-8", "replace")
