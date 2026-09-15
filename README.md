# corral

给交互式 AI 编程 agent（Claude Code、Codex 等）用的极简托管基础设施。

每个 agent 关在自己的栏位里：调用方按名字打开它、往里塞话、查状态、等它答完、取回复；人随时可以从任何终端接进去看、点对话框、插话。关掉窗口 agent 照样跑。

- 只提供机制，不带任何流程。
- 每个 agent 一个极小的栏位进程，没有中心服务。
- 状态靠 agent 自己的钩子，不读屏。
- 只用 Python 标准库。

状态：设计完成，试验未开始。

- 设计：[docs/DESIGN.md](docs/DESIGN.md)
- 试验清单：[docs/SPIKE.md](docs/SPIKE.md)
