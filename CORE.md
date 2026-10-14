# 小红书受众研究核心

增量扩样调度、私有队列与 CUA helper 见 [BATCH-WORKFLOW.md](BATCH-WORKFLOW.md)。

离线导入、SQLite 快照、去标识和描述性分析。仅使用 Python 3.10+ 标准库，不包含登录、采集或自动发帖功能。模板是空数据/字段说明，模拟观测仅存在于 `tests/`，不作为研究成果。

## 使用入口

在本目录执行：

```powershell
python -m xhs_research init --db data/private/research.sqlite
python -m xhs_research import --db data/private/research.sqlite --input data/private/authorized-observations.json
python -m xhs_research validate --db data/private/research.sqlite --study STUDY_ID --source-mode real
python -m xhs_research analyze --db data/private/research.sqlite --study STUDY_ID --source-mode real --output exports/report-data.json
python -m xhs_research select --db data/private/research.sqlite --study STUDY_ID --source-mode real --limit 24 --seed pilot-v1 --output data/private/selected-notes.json
python -m xhs_research sample --db data/private/research.sqlite --study STUDY_ID --source-mode real --note NOTE_ID --limit 30 --seed profile-v1 --output data/private/profile-worksheet.json
python -m unittest discover -s tests -v
```

`sample` 生成本地私有的研究内 HMAC 用户键抽样表，**不会**自动把用户标记为画像已核对。核对后在原始评论记录中填 `profile_sampled=true`，保留抽样规则、证据与观察时间，再导入。表中随机顺序是已加载池的确定性 hash 排序，不是整帖受众的随机抽样。`select` 按主题、粉丝档、帖子年龄和格式分层轮流抽取，单作者最多 3 篇。

## 输入契约

参照 `templates/empty-study.json`、`record-fields.json` 和 `envelope.schema.json`。所有输入显式填写 `study_id`、`owner_id`、`source_mode=real|synthetic` 和带时区的 `observed_at`；不同模式和所有者具有独立数据命名空间。支持单个 envelope、envelope 数组，或者每行一个 envelope 的 JSONL；单记录 JSONL 也支持：

```json
{"study_id":"STUDY_ID","owner_id":"OWNER_ID","source_mode":"real","observed_at":"2026-10-14T00:00:00+08:00","entity":"comments","record":{"comment_id":"NATIVE_ID","note_id":"NOTE_ID"}}
```

实体为 `searches/creators/notes/comments/outcomes`，分别必须有 `search_id/creator_id/note_id/comment_id/outcome_id`。note 必须关联 creator，comment 必须关联 note。可以分批导入；`validate` 检查关联完整性，缺失已加载父评论只作 warning。JSON Schema 是交换契约，运行时用标准库完成关键约束校验，不依赖 jsonschema。

用户身份仅接平台稳定 `user_id` 或小红书 `/user/profile/` URL；昵称、文本生成的临时 ID 不作为稳定键。原 ID 和主页 URL在写库前转换为研究/模式/所有者范围内的 HMAC，昵称移除；HMAC 密钥仅存本地 SQLite metadata。此为去标识化，并不宣称不可逆匿名。输入文件与数据库仍是私有资料，`data/`、`private/`、`exports/`、数据库及测试临时目录被 `.gitignore` 排除。工具拒绝 cookie/token/password/raw_profile/联系方式/私聊全文字段。

缺失只填 null 或省略。计数保存 `{raw,value,precision}`；“0.5万”永远为 rounded，即使同时提供 5000 和 exact，仍不能通过 ≤5000 门槛。精确 0–5000 均包含在门槛中；真实零粉保留 `value=0/precision=exact/eligible_fans=true`，归入 0–500 档，与未知/null 分开。零粉只使千粉比值不可计算（null），仍可在可比评论池内按 U 比较。普通互动计数的真实 0 保留。作者粉丝快照使用不晚于帖子观察时点的最新快照，后来涨粉不改写旧帖采集时粉丝。

性别证据用数组：`audience.gender=[{value:"female",basis:"public_profile",evidence:{source:"visible profile",locator:"gender field",observed_at:"..."}}]`。认可的性别/年龄 basis 为 `public_self_report/public_profile/authorized_aggregate`，必须有 evidence。value 为 female/male/other 或 unknown；年龄为 `18–24/25–34/35–44/45+/minor`，不填猜测。多条明确性别披露矛盾归 unknown。头像/姓名/语气推测 basis 不生效。明确未成年证据即使与成年信息冲突，仍保守排除成人目标指标。

作者自身的明确公开自述也可以来自作者本人帖子标签。例如确认 `#薄肌男孩` 是作者对本人的自述后，可填 `creator.gender=[{value:"male",basis:"public_self_report",evidence:{source:"author_note_tag",note_id:"原帖ID",locator:"platform_tags",value:"#薄肌男孩"}}]`，保留原帖和标签位置。不得仅因标签提到他人、账号属于健身/户外等主题，或人物外貌看似男性而升级作者性别；不明确时仍为 unknown。

兴趣：`public_self_report/public_content_repeated` 汇总为稳定 interests，`single_topic` 单独汇总为 topic_interests。`presented_style` 只描述本人公开呈现，`preferred_style` 必须本人明确表达偏好，不能混用。标签均要求 evidence。视觉标签记录 media_id、role=cover/later_image/video_segment、dimension/value、evidence、version、annotator、status；封面不能代表全组，未观看全片不标全片。

## 输出口径

`analyze` 输出 schema_version、study_id、source_mode、status、notes、summary、comparison_groups、data_quality、audience_aggregates、limitations。分析输出无评论者 ID、主页、评论全文或个人证据全文。公开博主与原帖链接可保留。`notes` 含 creator、content、coverage、visual_labels、metrics、comparison、main_sample、reference_case；报告层可基于这些字段独立渲染。

U 为稳定去重的、明确非作者的外部一级评论者；作者身份/层级/用户键未知不猜入 U，并单列数据质量。N 为其中实际核对画像的去重人数，资料未知仍留在 N；K 是明确性别人数，F 是明确女性人数。metrics 保留完整 N/K/F；`adult_target` 用同一口径排除明确未成年，年龄未知单列，`confirmed_adult_female` 只含有成年自报证据者。女性聚合画像排除明确未成年，跨帖按研究内用户键去重；小格少于 10 人 count=null、status=insufficient_sample。

F/K、K/N、F/N、未知范围 `[F/N,(F+N-K)/N]`、95% Wilson 下界同时输出，零分母均为 null。未知范围不是置信区间。女性有效评论按人计数，认可意图允许多标签；广告/无关/攻击和与其他评论者交流不算面向作者的有效互动。作者回复、楼中楼分别计数。

仅完整池、已说明 comparison_key、稳定键与作者身份无缺失时 pool_comparable=true。`U_per_1000_fans_observed` 是观察池描述，`U_per_1000_fans_comparable` 仅可比池输出；不叫阅读转化率。部分画像样本绝不估计全帖女性人数或输出每千粉女性有效排名指标；后者仅 whole_pool_profiled=true 且池可比时有值。不同 N 的女性有效绝对人数不能直接排名。

默认 matched_group_research 的保守参考阈值：精确粉丝门槛、男性主题明确证据、单作者主样本最多 3 篇、可比完整池、匹配组较高互动、adult_target N≥30/K≥20/K÷N≥60%、Wilson 下界>50%、女性有效≥10、公开来源链接。匹配组按主题/粉丝档/帖子年龄/格式/coverage key，至少 4 篇/2 位作者才初步比较 U 与每千粉 U 前 25%，并且必须存在较低表现对照；这只是初始保守实现，报告始终标 `initial_not_pilot_calibrated`，小试后需要冻结阈值。单帖不能凭先验进入 recommended。证据不足分 structure_reference/pending，不产生任意加权吸引力分。

## 自有账号私有分析

参照 `templates/owner-outcomes.json`，模板不含实际结果。`analyze --owner OWNER_ID` 才输出 owner_outcomes；同 study 有多个 owner 时必须显式指定 owner，不合并私有结果。记录制作总分钟、24h/72h/7d 窗口、阅读值及来源/口径、女性有效评论人数、女性主动私聊人数、归因等级。不得导入私聊全文。

女性成效必须有明确公开/授权性别证据且明确排除未成年；私聊另要求 `dm_initiation_rule=new_female_initiated_not_previously_contacted`。归因分 explicit_note/platform_source/period_unattributed/unknown；只有前两类能计算可归因每千阅读私聊。制作耗时零或未知、阅读零或来源口径缺失时效率/阅读指标为 null；未导入结果输出 unverified_no_data。授权记录仍是观察比较，不自动作因果结论。

## 快照与恢复

同实体同 ID 同观察内容重复导入幂等；不同观察时间或内容保留独立快照，当前统计取最新记录。同次导入出错整批事务回滚。再次导入合法分批数据即可续接，不删除旧快照。数据库文件包含 HMAC 密钥，备份须与私有数据一起保留，不公开提交或共享。删除原输入不删除 SQLite 中必要证据；本工具不自动执行保留期删除。
