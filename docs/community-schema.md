# 社区聚合包 v1

原始分析、评论、个人资料、私聊和凭证留在本地。此 CLI 无网络请求。导出允许名单之外的字段全部丢弃；接收包有任何额外字段则拒绝。公开帖子链接可以识别发布者，因此聚合包**不是匿名数据**，导出不等于对外发布授权。

```powershell
python community.py export --analysis data/private/analysis.json --dataset study-20261014 --output data/community/study-20261014.json
python community.py validate data/community/study-20261014.json
python community.py merge data/community/study-20261014.json data/community/other-study.json --db data/community/merged.sqlite --html data/community/index.html
python -m unittest discover -s tests -p test_community.py -v
```

`merge` 从本次传入的全部合法 JSON 包重建并替换两个输出，不读取旧 SQLite，也不接受数据库上传。保留旧研究时须再次传入其 JSON 包。输出路径不能与输入或彼此相同。重复包幂等；同一 `dataset_id` 不同 checksum 拒绝，修订观测须使用新 dataset ID。不同 dataset 的同一帖子保留为不同研究观测。输入全部验证通过后才写输出；每个输出单独原子替换，二者不是跨文件事务，文件系统写入失败时重新运行即可。无隐式正例筛选。

包根字段严格限定为：

| 字段 | 契约 |
| --- | --- |
| schema_version | 整数 `1` |
| dataset_id | 1–128 字符，首字符 ASCII 字母或数字，其余字母、数字、`_ . -`；视为一个 study |
| source_mode | `real` / `synthetic`，不猜测，分别展示 |
| selection_policy | `matched_group_research` / `high_comment_female_case_study` |
| records | 最多 10,000 个观测对象 |
| checksum | SHA-256 小写 hex，见下文 |

每个 records 对象严格限定为：

| 字段 | 契约及导出来源 |
| --- | --- |
| source_url | `https://www.xiaohongshu.com/explore/<1–64位小写hex>`；由 `note.url` 提取，移除 query/fragment，拒绝错域名、userinfo、port、非hex路径；不能用note_id猜缺失URL |
| observed_at | `note.observed_at`；ISO 8601日期时间、秒、可选1–6位小数，必须有`Z`或`±HH:MM`时区 |
| fans | 仅 `{value, precision}`，来自 `note.creator.fans`；precision=`exact/rounded/unknown`，unknown 时 value=null，不保留raw/source/evidence |
| platform_comment_count | 来自 `note.platform_counts.comments.value`，不可用为null |
| platform_comment_precision | `exact/rounded/unknown`，unknown时count=null；rounded绝不可解读为精确值 |
| captured_count | `note.metrics.observed_comment_count`；包含已捕获作者回复、嵌套评论，缺失为null，不是独立用户数 |
| N / K / F | `note.metrics.adult_target.N/K/F`，排除明确未成年后的已抽样用户数/有明确性别披露人数/女性人数；三者全null或全有值且 `F≤K≤N`；未知年龄不代表已确认成年 |
| coverage | `note.coverage.status`，`complete/partial/unknown`；缺失unknown |
| gender_evidence_method | 固定 `explicit_public_profile_or_self_report`；对应受支持本地analyze公开披露口径，不支持头像/姓名推断，checksum也不能证明证据真实性 |
| topic（可选） | `body_display/fitness/outdoor/daily/car/hobby/other/unknown`；从note.topics或content.topics精确映射有限标签，多个不同主题或未知为unknown |
| subject（可选） | `person/person_activity/object/landscape/mixed/unknown`；只取status=reviewed、role=cover的visual_labels有限映射，不保留标签原文或evidence |
| composition（可选） | `close_up/medium_shot/wide_shot/full_body/collage/unknown`；同样只取已审核封面标签 |

所有计数只接受 `[0, 2^53−1]` 整数或null，拒绝布尔、浮点、负数、NaN/Infinity。N不能超过已知captured_count。同包同URL同时间（含时区等价）重复拒绝。缺失N/K/F不退回包含未成年人的总样本，也不推算。导出保留全部notes，包括低评论、反例和未知。主题仅精确映射：身材展示→body_display，健身/健身运动/fitness_progress→fitness，户外/户外运动→outdoor，日常/生活日常→daily，汽车→car，兴趣爱好→hobby；英文enum也接受，其他不做文本推断。

HTML按 `source_mode` 和 `dataset_id` 分组，每条观测独立展示。描述性初筛要求N≥8、K/N≥60%；满足后F/K>50%为`positive`，否则为`counterexample`，不满足或数据缺失为`unknown`。这是选题初筛，不是最终女性多数证明、总体偏好或因果分析。禁止将跨帖N/K/F相加解释为独立用户；包内没有可跨帖去重的评论者标识。HTML对所有动态值转义。

checksum计算：删除根级checksum字段，使用Python `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` 后UTF-8编码取SHA-256。checksum校验内容一致性，不是签名或信任认证。单文件上限16 MiB，merge最多256路径且输入累计≤64 MiB；重复JSON键、额外字段、非法值或checksum不符均拒绝。CLI错误不回显原始内容、敏感URL或输入路径。

SQLite仅包含datasets元信息和observations允许名单payload，不含原始评论、评论者ID、用户名、头像、文案、自由文本标签、证据、私聊、密钥或cookies。发布前由提交者核对聚合数据与共享范围；这里的命令仅生成本地可审查产物。
