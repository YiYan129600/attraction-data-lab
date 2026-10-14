# 从零安装与使用

所有命令在**本仓库根目录**执行。先跑模拟示例，再接真实采集。离线分析不需要小红书登录、MCP、Docker、数据库服务器、模型 API Key 或付费服务。

## 1. 环境要求

| 组件 | 是否必须 | 说明 |
|---|---|---|
| Python 3.10 或以上 | 是 | 使用标准库；CI 使用 Python 3.12 |
| Git | 推荐 | 克隆、更新、提交 PR；只体验可用 GitHub「Code → Download ZIP」解压 |
| 浏览器 | 看 HTML 时需要 | 直接打开本地 HTML，无须启动 Web 服务 |
| Node.js | 否 | 仅执行可选 CUA helper 测试时使用；CI 使用 Node 22 |
| Codex 或其他支持 Skill 的 Agent | 否 | 手动运行 Python 不需要；由 Agent 辅助研究时使用 |
| 已授权的小红书读取环境 | 采真实数据时需要 | 用户已有 MCP/API 或已登录浏览器；它们不是本项目自带依赖 |

Python 可从 [Python 官方下载页](https://www.python.org/downloads/) 获取，Git 可从 [Git 官方安装页](https://git-scm.com/downloads) 获取。Windows 安装 Python 时启用 PATH 选项，安装后重新打开终端。

## 2. Windows PowerShell

先检查：

```powershell
python --version
git --version
```

若 `python` 不存在但安装了 Python Launcher，可使用 `py -3 --version`，并把下面创建环境的 `python` 换为 `py -3`。

```powershell
git clone https://github.com/YiYan129600/attraction-data-lab.git
cd attraction-data-lab
python -m venv .venv
& .\.venv\Scripts\python.exe -X utf8 demo.py
Invoke-Item .\exports\demo\report.html
Invoke-Item .\exports\demo\community.html
```

不需要激活虚拟环境，因此不会遇到 PowerShell `Activate.ps1` 执行策略问题；后续命令使用 `& .\.venv\Scripts\python.exe` 即可。**没有 pip install 步骤，也没有 requirements.txt，这是正常的。**

如果用 Download ZIP，解压后进入含 `demo.py` 的目录，从 `python -m venv .venv` 开始。

## 3. macOS / Linux

```sh
python3 --version
git --version
git clone https://github.com/YiYan129600/attraction-data-lab.git
cd attraction-data-lab
python3 -m venv .venv
.venv/bin/python demo.py
```

用浏览器打开 `exports/demo/report.html` 和 `exports/demo/community.html`。某些 Linux 发行版把 venv 单独打包；若无法创建虚拟环境，可按系统包管理器安装对应 Python venv 组件，或直接使用 Python 3.10+ 运行，因为本项目不安装第三方依赖。

## 4. 怎样确认跑通

示例应显示数据库 `valid: true`，然后生成三个完全虚构的帖子：正例、对照、未知各一个。最终输出包含 `Synthetic demo ready`。

| 文件 | 用途 | 是否上传 |
|---|---|---|
| `data/private/demo/observations.json` | 模拟输入 | 不上传 |
| `data/private/demo/research.sqlite` | 本地研究库，含研究去重密钥 | 不上传 |
| `data/private/demo/analysis.json` | 本地分析 | 不上传 |
| `exports/demo/report.html` | 内容分析 HTML | 默认本地 |
| `exports/demo/contribution.json` | 模拟的社区交换包 | 仅作演示，不混入真实研究 |
| `exports/demo/community.sqlite`、`community.html` | 社区重建结果 | 派生结果，由 CI 或本地构建 |

重复执行会复用本地快照并去重，不需要删除旧数据。

查看已发布的真实统计包：

```powershell
& .\.venv\Scripts\python.exe build_community.py
Invoke-Item .\exports\community.html
```

该命令从 `contributions/*.json` 重建 `exports/community.sqlite` 和 HTML。它不会把你的私人研究库加入公共数据库。

## 5. 安装 Skill（可选）

Skill 是给 Agent 的研究指导；Python 工具在本仓库，**只复制 Skill 并不会自动复制工具或安装采集器**。

Codex 用户可把 `skills/attraction-audience-research` 整个文件夹复制到自己的 Codex 技能目录。默认常见位置为 `~/.codex/skills/`，自定义 CODEX_HOME 时使用其 `skills/` 子目录。例如 PowerShell：

```powershell
$skillRoot = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex/skills' }
$skillTarget = Join-Path $skillRoot 'attraction-audience-research'
if (Test-Path -LiteralPath $skillTarget) {
    Write-Output '技能已存在，请先比较本地修改，再选择是否更新。'
} else {
    New-Item -ItemType Directory -Force $skillRoot | Out-Null
    Copy-Item -LiteralPath './skills/attraction-audience-research' -Destination $skillTarget -Recurse
}
```

macOS / Linux 可以用文件管理器复制到 `${CODEX_HOME:-~/.codex}/skills/` 对应的实际目录。其他 Agent 按其支持的 Skill 目录安装，不要假设目录相同。重新打开会话或刷新技能列表后调用：

> 使用 $attraction-audience-research。工具仓库在【填写你的实际克隆路径】。先复用已有数据，再研究健身、户外、生活场景的异性主动评论；目标 30 篇正例，同时保留匹配反例，每帖尽量 100 条评论。生成 HTML，原始数据留本地，导出可审查的社区统计包。

## 6. 真实采集怎么配置

三条路径任选一条，不需要全部安装：

| 路径 | 要准备什么 | 如何交给分析层 |
|---|---|---|
| 手工或已有资料 | 有权使用的帖子与观察记录 | 按 `templates/empty-study.json` 和 `record-fields.json` 填写 |
| 已授权浏览器 | 能正常查看内容的浏览器会话；Agent 环境支持对应操作 | 按 [采集契约](../skills/attraction-audience-research/references/collection.md) 保存 capture，再执行转换器 |
| 已有 MCP/API | 已安装、正常登录且允许读取的服务 | 核对实际返回字段，转成统一 envelope；不能把原始响应直接当本项目格式导入 |

MCP 和内置浏览器可能使用不同登录态。用户在工具支持的正常登录界面自行登录；不复制 Cookie 到仓库。第三方工具的安装与接口以其当前文档为准，本项目不保证某个版本长期可用，也不重复分发浏览器大文件。

先验证一篇搜索→详情→作者→评论→导入；有验证码、403 或权限要求则停止该读取分支，继续已有数据分析。**模拟示例通过不等于真实采集已经配置成功。**

使用本地证据桥时，在一个终端运行：

```powershell
& .\.venv\Scripts\python.exe browser_capture_bridge.py
```

打开终端显示的回环地址（默认 `http://127.0.0.1:18766/`），通过表单保存无凭证 capture 数组。保留终端运行，用 Ctrl+C 停止。桥只是保存证据，不负责登录或抓取；端口冲突时先查已有桥服务，勿停止不明进程。

已有 capture 后：

```powershell
& .\.venv\Scripts\python.exe import_browser_captures.py --study my-study-v1 --owner local-research
```

或者手工 envelope 放到 `data/private/observations.json` 后：

```powershell
& .\.venv\Scripts\python.exe -m xhs_research import --db data/private/research.sqlite --input data/private/observations.json
& .\.venv\Scripts\python.exe -m xhs_research validate --db data/private/research.sqlite --study my-study-v1 --source-mode real
& .\.venv\Scripts\python.exe -m xhs_research analyze --db data/private/research.sqlite --study my-study-v1 --source-mode real --selection-policy high_comment_female_case_study --minimum-platform-comments 50 --output data/private/analysis.json
& .\.venv\Scripts\python.exe reporting.py --analysis data/private/analysis.json --output exports/local-report.html
```

`--study` 必须与输入 study_id 一致；例子的 my-study-v1 需要替换为自己的 ID。同研究有多个 owner 时，analyze 增加 `--owner` 指定，不混合私人结果。

## 7. 怎样贡献数据

```powershell
& .\.venv\Scripts\python.exe community.py export --analysis data/private/analysis.json --dataset my-study-v1 --output contributions/my-study-v1.json
& .\.venv\Scripts\python.exe community.py validate contributions/my-study-v1.json
git diff -- contributions/my-study-v1.json
git add -- contributions/my-study-v1.json
git diff --cached --stat
```

只暂存检查后的单个统计包，不使用 `git add .` 上传研究目录。检查后通过 Fork/PR 提交，填写抽样说明；详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。公开包包含帖子链接、粉丝/评论计数、N/K/F、主题及有限构图标签；没有评论者 ID、原文、文案、私聊和凭证。

公共数据库下载：仓库 Actions → 最新成功的 “Validate research and build community database” → Artifacts → `community-database-and-report`。也可以运行 build_community.py 本地重建；不需要上传或下载私人 SQLite。

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| 找不到 python | 重开终端；Windows 尝试 `py -3`；或用实际 Python 可执行文件完整路径 |
| `No module named xhs_research` | 确认当前目录含 `xhs_research/`；不要在 scripts/ 或 skills/ 内运行主命令 |
| PowerShell 激活被禁止 | 使用文中的虚拟环境可执行文件路径，无需修改执行策略 |
| 中文输出乱码 | Python 命令增加 `-X utf8`；输入文件使用 UTF-8 |
| archive/file not found | 使用文件实际路径，不能默认文件在 Downloads；本项目离线功能不需要浏览器 ZIP |
| demo 成功但采集失败 | 分别检查实际读取工具、登录态和页面权限；这是两个独立环节 |
| 不够 100 条 / 女性比例未知 | 保留实际数量、partial 和 unknown，不能补造或从头像猜测 |
| community 导出失败 | 检查 analysis 来源、帖子公开 URL、时间与数字口径；详细契约见 community-schema.md，不把原始响应直接导出 |
| 同 dataset_id 冲突 | 不覆盖已提交批次；修订使用新 ID，在 PR 解释替代关系 |

## 9. 更新与开发验证

更新前先检查 `git status`，保留自己的未提交修改，再 `git pull --ff-only`。不要删除 data/private 来“重装”。Skill 是独立复制件，需要比较并同步更新。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_cua_batch_helpers.js
```

第一条测试核心、报告和社区交换，第二条仅需 Node 的可选浏览器辅助测试。自动测试不证明某个真实页面当前可采集。
