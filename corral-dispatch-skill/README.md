# corral-dispatch

给主控用的编排技能：按任务文件拆任务，用 corral 把活派给 Claude Code、Codex 等 agent，再审查、合并、收尾。依赖 corral 技能（`corral install-skills` 安装）；corral 程序本身不引用它。

- `SKILL.md`：技能正文。
- `项目AGENTS模板.md`：项目的 AGENTS.md 里要加的一节，让主控在你说「开始」时自动按本技能分派。
- `route.py`：路由。主控拆完每件活调一次，由 TypeSafe 的分类模型建议交给哪家、几档、要不要交叉审查；拿不准或调不通时主控自己判断。只用标准库。

## 路由的 key

在 `~/.zshrc` 里加 `export TYPESAFE_API_KEY=<key>`。corral 开 agent 时从登录 shell 重建环境，之后开的主控都带着它；加 key 之前开的主控要重开。没有 key 时路由不可用，主控照 SKILL.md 第 3 节自己判断，分派照常。

发给 TypeSafe 的只有主控写的三五句任务摘要，不发任务文件。某个项目不想发，在它的 AGENTS.md 里写一句「不用路由」。

## 安装

用软链接，把两家的用户级技能目录都指向这个文件夹，只保留一份正文：

```sh
ln -s <仓库路径>/corral-dispatch-skill ~/.agents/skills/corral-dispatch   # Codex
ln -s <仓库路径>/corral-dispatch-skill ~/.claude/skills/corral-dispatch   # Claude Code
```

- 装好后，新开的会话就能看到。2026-09-19 实测 Claude Code 2.1.277、Codex 0.155.1 都认软链接。
- 以后改完提交就生效，不用重装。仓库里没提交的改动也会立刻被读到，所以改技能时在分支里改，或者改完尽快提交。
- 仓库不在这台机器上时，改成复制整个文件夹，以后每次更新都要重新复制。

## 卸载

```sh
rm ~/.agents/skills/corral-dispatch ~/.claude/skills/corral-dispatch
```

末尾不要加 `/`：这样删的是链接本身，不会动仓库里的文件。

## 在项目里启用

照 `项目AGENTS模板.md` 在项目的 AGENTS.md 里加上「开发方式（主控分派）」一节。想确定这一次一定走本技能时，也可以点名调用：Claude Code 用 `/corral-dispatch`，Codex 用 `$corral-dispatch`。
