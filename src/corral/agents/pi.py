"""pi：用 --extension 按启动参数加载钩子扩展（TypeScript，由 pi 自己的运行时执行），和用户自己的扩展共存。

扩展把 pi 的事件翻译成 corral 现有的事件名直接写事件文件，见 hook_pi.ts 和 DESIGN 第 12 节。
启动即触发会话开始；首句作为位置参数交给 pi；收到 SIGHUP 触发 session_shutdown 并退出。
"""


class Pi:
    kind = "pi"
    hook_file = "hook_pi.ts"
    needs_hook_python = False
    # Ctrl-C 第一次只清空输入框；SIGHUP、SIGTERM 都会正常收尾退出
    quit_steps = ({"signal": "HUP", "wait": 5}, {"signal": "TERM", "wait": 3})

    def build(self, argv, hook_path, prompt=None):
        out = [argv[0], "--extension", hook_path, *argv[1:]]
        if prompt is not None:
            out += ["--", prompt]  # 同 Claude Code：-- 之后才是首句，不会被选项吞掉，也允许以 - 开头
        return out
