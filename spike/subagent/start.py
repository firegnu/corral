"""试验启动：进程内给 Claude 多注册 SubagentStart / SubagentStop，钩子换成本目录的 hook.py，再走 corral start。
用法同 corral：CORRAL_HOME=/tmp/cs python3 spike/subagent/start.py start demo/sub --cwd /tmp/cs-w -- claude ...
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "src"))

import corral.agents.claude as claude  # noqa: E402
from corral import spawn  # noqa: E402
from corral.cli import main  # noqa: E402

claude.CLAUDE_EVENTS = claude.CLAUDE_EVENTS + ("SubagentStart", "SubagentStop")
spawn.HOOK_DIR = HERE

sys.exit(main())
