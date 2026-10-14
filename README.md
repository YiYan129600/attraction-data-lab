# Attraction Data Lab · 异性吸引力数据实验室

用数据研究什么内容更容易获得异性主动评论，以及哪些内容没有效果。把调研变成可复核、可增量积累的共同研究：**本地采集与分析 → 导出统计包 → GitHub PR → 合并公共数据库和 HTML 报告**。

当前实现面向小红书，研究小体量创作者的内容与评论受众。包括正例、反例、证据不足案例；不把高互动案例包装成涨粉或私聊效果保证。

**新用户先看：[从零安装、环境配置与使用教程](docs/INSTALL.md)**，包含 Windows / macOS / Linux、Skill 安装、真实采集接入和常见报错。

## 先跑一个完整示例

需要 Python 3.10+，核心与报告均只用标准库。无需数据库服务器、付费 API 或模型密钥。

```sh
git clone https://github.com/yiyan129600/attraction-data-lab.git
cd attraction-data-lab
python demo.py
```

打开 `exports/demo/report.html` 看本地内容分析，打开 `exports/demo/community.html` 看社区统计。示例明确标记 `synthetic`，不属于真实调研。重复执行不会重复计入评论者。

## 开始自己的研究

1. 读 [研究方法](skills/attraction-audience-research/references/research.md)，冻结主题、抽样规则、正反例配额和停止条件。
2. 按 [采集接入](skills/attraction-audience-research/references/collection.md) 复用已授权读取工具。现有 MCP 可用则批量读取；否则用已授权浏览器保存可见证据，或手工填写输入模板。**本项目不是开箱即用的全站爬虫**，不捆绑第三方浏览器或登录凭证。
3. 用 `templates/empty-study.json` 和 `templates/record-fields.json` 组织数据，放在 `data/private/`。字段与计数口径见 [CORE.md](CORE.md)。

```sh
python -m xhs_research import --db data/private/research.sqlite --input data/private/observations.json
python -m xhs_research validate --db data/private/research.sqlite --study MY_STUDY --source-mode real
python -m xhs_research analyze --db data/private/research.sqlite --study MY_STUDY --source-mode real --selection-policy high_comment_female_case_study --minimum-platform-comments 50 --output data/private/analysis.json
python reporting.py --analysis data/private/analysis.json --output exports/local-report.html
```

`reporting.py` 的内容报告包含帖子文案与创作者资料，供本地研究；它不是社区上传包。社区报告保留反例和未知，使用下面的严格导出链路。

## 贡献公共数据库

```sh
python community.py export --analysis data/private/analysis.json --dataset my-study-v1 --output contributions/my-study-v1.json
python community.py validate contributions/my-study-v1.json
python community.py merge contributions/my-study-v1.json --db exports/community.sqlite --html exports/community.html
```

通过 PR 提交 `contributions/*.json`，不要上传原始 SQLite、评论、评论者资料或登录数据。维护者合并后，CI 从全部统计包重建公共 SQLite 与 HTML，供下载和二次分析。详见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [交换格式](docs/community-schema.md)。

公开包保留帖子来源链接，因此不是匿名数据；它不包含评论者名单。不同研究可能观察同一批人，不能跨包累加为全站独立受众人数。真实与模拟数据始终分开。

## 作为 Agent Skill 使用

将 `skills/attraction-audience-research/` 复制到你的 Agent 技能目录，或通过支持 GitHub 子目录安装的技能安装器安装。Codex 常用目录为 `~/.codex/skills/attraction-audience-research/`。同时保留此仓库作为工具工作目录。

调用示例：

> 使用 $attraction-audience-research，在 attraction-data-lab 仓库中研究户外、健身和生活场景。目标 30 篇女性较多案例，另外保留匹配反例，每篇尽量采集 100 条评论；复用缓存，输出 HTML 和可贡献的聚合包。

技能会根据已有工具选择采集方式，不假设新用户拥有 Codex 特定浏览器、已登录小红书或已安装 MCP。

## 项目内容

| 入口 | 用途 |
|---|---|
| `xhs_research/` | 本地 SQLite 快照、研究内去重、抽样和描述统计 |
| `reporting.py`、`report_assets/` | 离线内容分析 HTML |
| `community.py` | 严格白名单导出、校验、公共数据库与 HTML |
| `batch_workflow.py` | 增量队列、缓存、检查点、成本记录 |
| `browser_capture_bridge.py`、`cua_batch_helpers.js` | 可选浏览器证据保存与批量读取辅助 |
| `skills/attraction-audience-research/` | 可迁移的完整研究与分析 Skill |
| `docs/pilot-archive.md` | 首轮研究归档口径与局限 |

分析依据公开明确披露或授权聚合资料，不以头像、昵称推断性别或年龄。年龄未知不能当作成年。单帖评论数不等于去重评论者数，公开表达不等于真实私聊。

## 验证与许可

```sh
python -m unittest discover -s tests -v
node tests/test_cua_batch_helpers.js
```

Node 只用于可选浏览器 helper 的离线测试，运行核心不需要 Node。测试不证明第三方站点当前可访问。

代码为 [MIT](LICENSE)。社区统计包的使用与提交约定见 [数据许可](DATA-LICENSE.md)。不打包第三方爬虫源码、平台图片或原始评论。
