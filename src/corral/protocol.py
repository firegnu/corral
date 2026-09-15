"""命令和栏位之间的协议。

连接后客户端先发一行 JSON 请求（带 proto），栏位回一行 JSON 应答（带 proto）。
接入（attach）时应答之后变成数据流：栏位 → 客户端是原始输出字节；客户端 → 栏位是分帧消息。
协议只做加法；要删改语义就升 PROTOCOL_VERSION（兼容规则见 DESIGN 5.4）。
"""
import json
import struct

PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOLS = (1,)   # 命令能操作的栏位协议版本：当前和上一个（DESIGN 5.4）

FRAME_INPUT = b"i"    # 接入窗口里的输入字节
FRAME_RESIZE = b"r"   # 接入窗口尺寸：>HH（行、列）

MAX_LINE = 16 * 1024 * 1024


def encode_line(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def request_line(op, **fields):
    return encode_line({"proto": PROTOCOL_VERSION, "op": op, **fields})


def encode_frame(typ, payload):
    return typ + struct.pack(">I", len(payload)) + payload


def decode_frames(buf):
    """从缓冲区里取出完整的帧，返回 (帧列表, 剩余字节)。"""
    frames = []
    while len(buf) >= 5:
        n = struct.unpack(">I", buf[1:5])[0]
        if len(buf) < 5 + n:
            break
        frames.append((buf[:1], buf[5:5 + n]))
        buf = buf[5 + n:]
    return frames, buf


def encode_resize(rows, cols):
    return encode_frame(FRAME_RESIZE, struct.pack(">HH", rows, cols))


def decode_resize(payload):
    return struct.unpack(">HH", payload)
