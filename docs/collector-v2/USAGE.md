# Collector V2：运行与恢复

在仓库根目录使用Python 3.10+。标准库实现，无新增pip依赖。PowerShell可用 `& .\.venv\Scripts\python.exe` 替换下文python；尚未建虚拟环境时见[安装说明](../INSTALL.md)。

## 无登录的完整示例

```powershell
python -X utf8 collector_demo.py
```

两篇帖子与人物全部虚构，source_mode=synthetic。模拟首次8人女性比例高、深查30人后男性更多，并展示跨帖资料缓存。生成的scope/run-id由最后摘要提供；文件在 `data/private/collector/<scope>/runs/<run-id>/`。重复执行生成新run，复用同一研究缓存；synthetic不混入real。

## 使用自己的捕获或资料

```powershell
New-Item -ItemType Directory -Force data/private | Out-Null
Copy-Item templates/collector-config.json data/private/collector-config.json
python -X utf8 -m xhs_research.collector doctor --config data/private/collector-config.json
python -X utf8 -m xhs_research.collector plan --config data/private/collector-config.json
python -X utf8 -m xhs_research.collector run --config data/private/collector-config.json --provider replay --captures <实际捕获目录>
```

编辑配置的study_id、owner_id、source_mode与captures。真实资料使用real；声明synthetic的示例不能以real导入。captures支持单个capture JSON或目录中的capture-*.json，格式沿用已有DOM契约。首次选择配置最大数量的不同帖子；多个主题有固定候选配额，未填满会保留缺口，不把首个主题填满全部容量。

plan输出的是上限与已有档案能力，不表示已经采集。doctor默认只查配置/本地回放，不读取凭证，不调用网络。

## 对接已经可用的本地服务

配置provider改为xhs-rest，base_url填真实回环服务地址，credentials_file填写已有本地客户端凭证文件路径；文件格式仅需base_url（可选）和token。不要把token填进公开配置模板，也不要打印文件内容。配置state_root如自定义，必须在当前仓库data/private内。

```powershell
python -X utf8 -m xhs_research.collector doctor --config data/private/collector-config.json --live
python -X utf8 -m xhs_research.collector run --config data/private/collector-config.json --provider xhs-rest --live --max-candidates 20
```

服务必须由用户现有环境正常启动并登录。collector不启动、安装或登录浏览器；不复制Cookie。不加--live，REST不触网也不读取凭证。一个端口只有一个runner，localhost与127.0.0.1及不同研究共用服务锁。

当前上游结构的局限：用户主页API需要用户签名，而标准搜索/评论User结构未返回它；整数gender也没有公开披露存在性。缺签名记needs_locator，缺披露证据记unknown/人工核对，不能借帖子签名猜用户签名，不能把0当女性。帖子、评论与统计链路可以独立运行，不能因此声称完整自动画像成功。

可先用用户明确授权的DOM捕获跑replay，再在**相同owner/study/source_mode**的新run接REST，复用时效内真实核对证据；不同使用者/研究默认隔离。缓存保留原时间，成功无披露、过期和访问失败分开。旧失败之后返回的更早资料不能冒充新的成功。

## 中断与预算

```powershell
python -X utf8 -m xhs_research.collector resume --config data/private/collector-config.json --run <run-id>
python -X utf8 -m xhs_research.collector resume --config data/private/collector-config.json --run <run-id> --live
python -X utf8 -m xhs_research.collector report --config data/private/collector-config.json --run <run-id>
```

resume配置必须与manifest冻结的业务配置相同。累积预算继续计数，不因恢复重置；预算用完时新建明确配置的新run复用证据，不改变旧run数字。源签名仅在内存，重启时通过原查询正常重新发现；签名无法恢复则deferred，不保证无条件续跑。

只有外部条件改变后，才可显式 `resume ... --retry-blocked`；它在正常探针通过后重开blocked/needs_locator任务。私密/删除、门槛缺失、已排除样本不会因这个选项自动变合格。

CLI退出码：0表示命令完成；2配置/本地输出错误；3读取服务或权限停止；4预算停止。即使3/4，已有证据与报告也保留。查看摘要stop_reason，不能只看“文件生成”。

默认上限20候选、300次Provider调用、240次主页操作、6篇深查、1800秒采集循环。调用重试计数；报告生成与捕获初始化单列解释，不当作网络吞吐。上限不是必须完成数量。401/403/429/验证码/登录失效停止，不换账号或代理。短暂超时/5xx最多一次退避，受剩余预算限制。

## 查看结果

| 文件 | 内容 |
|---|---|
| manifest.json | 冻结配置、策略、数据模式 |
| capabilities.json | 实际Provider探针的能力与绑定版本；不是安装版本已经验证 |
| evidence/*.json | 去凭证证据、内容哈希、导入与任务效果 |
| run-summary.json | 访问、缓存、计时、漏斗、分组迁移、每帖评论/画像分母 |
| run-report.html | 全部候选漏斗和已导入帖子的聚合表（含反例/未知） |
| content-report.html | 本地详细内容报告，可能突出案例筛选结果 |
| all-cases.html | 原社区聚合表，不含未读取详情的候选 |
| manual-tasks.json | 门槛/定位与视觉核对缺口 |
| contribution.json | community.py白名单导出，供审查后贡献 |
| community.sqlite | 本次包的派生公共结构数据库 |

全部默认保存在私有run目录，不自动上传。只有审查过的contribution.json可移入contributions；原始research.sqlite、state.sqlite、证据、详情HTML和配置不能直接上传。

N/K/F由既有core计算。捕获总条数、一级、回复、层级未知、作者回复与作者总评论分开；平台总数不同于捕获数。每帖固定选择8人，深查总上限30；未知或未成年不会替换补位。源无真正游标时扩展会重复加载，新增唯一评论与更新快照分别统计。

token缺少可核验来源时为null；collector没有模型调用，不等于整个coding任务token为0。回放速度、mock吞吐与温缓存结果不能用来声称真实站点提速。
