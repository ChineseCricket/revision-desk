# Revision Desk · 论文返修审阅台

作者在浏览器里对照原文，逐项接受、拒绝、评论或要求修改。Agent 用命令行提交候选、读取讨论、按反馈出下一版。每条决定都绑定到当时看到的版本。稿件、意见和 SQLite 历史由使用者保存在自己的电脑上。

## 一分钟试用

需要 Python 3.9+。网页资源随仓库提供，启动审阅台只用 Python 标准库。从仓库根目录执行：

```sh
python server.py --demo
```

打开 <http://127.0.0.1:8540/>。示例是一份虚构的花园观测记录。点击右侧「修改 1」，切换中间的「送审原稿 / 当前稿件」，查看同一候选在两份稿件的位置。在讨论框输入“请注明是否覆盖全部四个样地”，点「要求修改」。示例状态保存在 `.local/demo/`，下次启动可接着审。

在第二个终端读取反馈：

```sh
python agent.py queue
python agent.py history P-01
```

将下列 VERSION 和 N 分别换成上一次读取的 `item.version` 和 `state.revision`。发表回复后，返回的 `seq` 就是新的 N：

```sh
python agent.py reply examples/reply.txt --item P-01 --agent editor --version VERSION --revision N
python agent.py history P-01
python agent.py publish examples/change-v2.json --version VERSION --revision NEW_N
```

点页面右上「刷新历史」。原评论仍在「讨论历史」，新版回到待审。接受新版后，可在「落实与核验」里登记实际修改位置和核验证据。可在左列按意见筛选并验收整组，或从页首导出完整记录。服务端口可用 `--port 8541` 更改，客户端把同一选项放在子命令前。

## 换成自己的稿件

1. 在仓库**外面**建立一个私人目录，复制 `examples/garden/project.json` 作为模板。填写项目名称、任务及审稿意见原文，各自分配稳定且唯一的 ID。
2. 将送审稿和工作稿分别以 UTF-8 文本或 TeX 文件放入目录。设置 `original.source` 和 `current.source` 为相对路径。只有一份稿时可让两项指向同一文件。纯文本模式删除两个 `pages` 字段，或设为 `[]`。
3. 有编译好的 PDF 时，可在本地用下面的可选命令生成页图。把输出的 JSON 数组填入对应版本的 `pages`。原稿和当前稿分别生成；矩形使用**生成后 PNG 的像素坐标**。
4. 启动 `python server.py --workspace "../my-review"`。新工作区的队列为空。给 agent 仓库路径、工作区路径与端口，它可以按下一节发布第一条候选。

```sh
python -m pip install pymupdf
python render_pages.py original.pdf "../my-review" original-pages
```

## 接入自己的 agent

对于能执行 shell 命令的 Codex、Claude Code、Hermes 或其他 agent，将以下文字交给它即可。也可把整个 `skills/manuscript-review/` 目录复制到所用 agent 的技能目录，并告知它仓库位置。宿主有项目指令文件时，把指向此 skill 的一行加入现有说明即可。

> 阅读 Revision Desk 仓库里的 skills/manuscript-review/SKILL.md。审阅工作区是［我的稿件目录］，端口 8540。先读取队列和审稿意见，以独立候选提出修改，给我逐项审阅的本地链接。下一轮先读取我留在该版本上的反馈。

`agent.py` 输出 UTF-8 JSON，失败时返回非零退出码。无需 SDK、账号或模型 API key。远程 agent 可以在其已有权限的同机终端执行命令。服务监听本机回环地址，参与者共享本机信任，署名由参与者提供。

| 操作 | 命令 |
| --- | --- |
| 新候选 | `python agent.py publish proposal.json` |
| 所有现行候选及状态 | `python agent.py queue` |
| 一个候选的全部版本、事件与当前状态 | `python agent.py history ID` |
| 带署名的讨论回复 | `python agent.py reply note.txt --item ID --agent NAME --version VERSION --revision N` |
| 发布同一候选的下一版 | `python agent.py publish proposal.json --version VERSION --revision N` |

**候选格式**见 [examples/change.json](examples/change.json)。必填字段为 `id`、`task`、`title`、`body`。可选 `issues` 引用配置中的意见 ID。类型 `kind` 可为正文或图表修改 `change`、审稿回复 `reply`、待作者确认的问题 `question`。独立取舍分开编号，同一取舍改版沿用 ID。全文依据放入 `body`，可使用 Markdown。

`edits[]` 支持多个对照框。每个框给出 `before` 和 `after`，删除文字时 `after` 用空串。可选 `line` 和 `current_line` 从 1 起算，前者与原稿引文核对。页图锚点 `anchor` 和 `current_anchor` 各自包含 `page`（从 1 起）及 `rect: [left, top, right, bottom]`。缺少可靠定位时省略相应字段，文字提案也能审。

设置 `review_deferred: true` 可暂缓审批并继续讨论。撤回时使用同一 ID 发布新版本，设 `lifecycle: "withdrawn"` 并填写 `withdrawal_reason`。在「旧提案」筛选下可查撤回记录。

## 保存、冲突和换稿

- 完整备份请停止服务后复制**整个私人工作区**，含 `.reviewdesk/`。JSON 导出适合阅读和分析。重启同一工作区会恢复数据库中的所有版本与事件。
- 发布新版、批量审批和讨论提交都有版本或修订号检查。HTTP 409 表示有人先更新了记录，或源文件已经变动。重新读取当前记录再决定如何继续。
- 修改当前稿或页图后重启服务，使新内容生效。旧定位在当前稿指纹变化时自动隐藏，送审稿定位仍可用。需为新版重新标注时，发布带新位置的候选并重新审阅。
- 原始送审稿、原稿页图、任务或审稿意见构成固定基线。已有记录的工作区若要改变这组基线，请在新的私人目录开始一轮，保留旧目录作为审阅档案。
- 此仓库沿用原工具的双版本阅读与 SQLite 审批机制，并将原先依赖具体论文的导入脚本替换成 project.json 和通用发布命令。稿件的实际修改、编译和向期刊提交由原项目流程处理。

## 验证与许可

```sh
python -m unittest discover -s tests -v
```

网络受限的构建环境可运行 `python tests/offline_cycle.py`，用内存 HTTP 请求执行同一组服务端、CLI 和数据库断言。

核心软件采用 [MIT License](LICENSE)。随网页分发的 Marked 许可见 [第三方声明](THIRD_PARTY_LICENSES.md)。
