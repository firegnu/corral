"""omp（Oh My Pi，pi 的分支）：和 pi 一样用 --extension 按启动参数加载钩子扩展 hook_omp.ts。

omp 的事件和 pi 同源但有差别（没有 agent_settled、审批事件不同、有子会话），所以扩展单独一份，见 DESIGN 第 12 节。
启动即触发会话开始；首句作为位置参数交给 omp。
"""


class Omp:
    kind = "omp"
    hook_file = "hook_omp.ts"
    needs_hook_python = False
    quit_steps = ({"signal": "HUP", "wait": 5}, {"signal": "TERM", "wait": 3})

    def build(self, argv, hook_path, prompt=None):
        out = [argv[0], "--extension", hook_path, *argv[1:]]
        if prompt is not None:
            out += ["--", prompt]
        return out
