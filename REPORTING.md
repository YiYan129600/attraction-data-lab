# 离线报告与兴趣匹配

先用核心 CLI 从按来源隔离的 SQLite 研究库生成 `analyze` JSON，再运行：

```powershell
python reporting.py --analysis data/analysis.json --output data/report.html
```

直接用浏览器打开 HTML，无需服务、网络、新依赖。HTML 自包含 CSS/JS，不请求远程图片、字体、接口或服务。账号和帖子链接只允许 HTTPS 小红书域名（含 xhslink.com），剥离查询参数与 fragment；某些原帖可能需要用户从平台正常访问。报告不导入 Cookie，也不嵌入评论者 ID、主页、简介证据、私有 outcomes 或媒体源。

接口是 `xhs_research analyze` 的对象：`schema_version/study_id/source_mode/status/notes/audience_aggregates`。每个 note 消费公开 `creator`、`content`、`coverage`、`metrics`、`comparison`、`reference_case`、`visual_labels`。采用显式字段白名单；任何未列入接口的字段不会进入 HTML。输出重新计算 F/K、K/N、F/N 与未知范围，缺失或零分母保持不可计算。粉丝非 exact 时精确值不显示。完整原始研究资料只留在受限本地数据库，报告导出不是原始资料备份。

主题、粉丝档、主体、构图、案例类型、兴趣与正文关键词可组合筛选。兴趣九类来自内容 topics / 真实 tag 与固定词表的**主题关联**，不是对女性年龄、偏好或关系意愿的推断。受众聚合显示核心统计的稳定兴趣、单次话题兴趣和未知；小格不足 10 人隐藏人数。

研究使用 `selection_policy=high_comment_female_case_study` 时，只展示通过“平台显示评论≥50、公开明确性别初筛女性多数”筛选或仍待确认的候选，筛除项不展开深入案例，保留审计数量。头像、昵称不作性别证据；最终女性指标仍展示 N/K/F、覆盖和未知。这是高评论女性多数案例观察，没有低表现对照，不能作因果、提升效果或全平台结论。旧默认研究策略保持原有展示口径。

每位使用者在 HTML 单独选择真实兴趣、已有资源、出镜方式、每周时间。输入仅在当前页面内存中，不保存、不共享、不跨用户合并 outcomes。按对应资源与时间可行性生成最多三个方向，引用粉丝条件和男性证据确认的真实推荐/结构案例数量；没有案例时明确“待验证假设”。只建议已有车辆/宠物的真实活动，不推荐为复制内容购买车辆或宠物。分钟数是方案预算估计，须由使用者记录实测；案例数量不是效果评分，主题关联不证明受众偏好。

自有账号实验数据请使用核心工具的 `outcomes` 导入模板（见 `templates` 与核心 README），每位使用者保留自己的 `owner_id`，按 owner 查询。输入只包含获授权的聚合字段，例如制作耗时、阅读口径、有效女性评论、新发起主动私聊与帖子归因等级、24h/72h/7d 窗口；不需要私聊全文。公开报告不会包含这些私有记录；长期私聊效果与跨用户泛化未验证。

验证入口：`python -m unittest discover -s tests -p test_reporting.py -v`。JavaScript 行为测试使用环境现有 Node；无 Node 时明确 skip，不安装依赖。模拟输入只存在测试内存 / 临时目录，不进入真实研究结果。
