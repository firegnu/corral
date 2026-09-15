"""退出码和错误类型。退出码是对外契约的一部分（docs/CONTRACT.md）。"""

CONTRACT_VERSION = "1"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
EXIT_NOT_DELIVERED = 3
EXIT_TIMEOUT = 4
EXIT_EXISTS = 5
EXIT_SANDBOX = 6
EXIT_NOT_IDLE = 7
EXIT_HUMAN_ACTIVE = 8
EXIT_INCOMPATIBLE = 9

EXIT_NAMES = {
    EXIT_OK: "ok",
    EXIT_ERROR: "error",
    EXIT_NOT_FOUND: "not_found",
    EXIT_NOT_DELIVERED: "not_delivered",
    EXIT_TIMEOUT: "timeout",
    EXIT_EXISTS: "exists",
    EXIT_SANDBOX: "sandbox",
    EXIT_NOT_IDLE: "not_idle",
    EXIT_HUMAN_ACTIVE: "human_active",
    EXIT_INCOMPATIBLE: "incompatible",
}


class CorralError(Exception):
    """带退出码的错误。error 是机器可读的标识，message 给人看。"""

    def __init__(self, exit_code, error, message, **extra):
        super().__init__(message)
        self.exit_code = exit_code
        self.error = error
        self.message = message
        self.extra = extra

    def to_json(self):
        # 附加字段在前：不管带了什么字段，ok / error / message 都不会被盖掉
        return {**self.extra, "ok": False, "error": self.error, "message": self.message}
