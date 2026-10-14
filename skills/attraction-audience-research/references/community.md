# 归档与社区共享

原始捕获、导入 envelope、本地 SQLite、分析 JSON、失败日志均在 `data/private/` 保存。保留观测时间、采样规则、来源文件和校验摘要；相同内容重复导入幂等，新观察留新快照。它们不应被 git add；SQLite 内含 HMAC 密钥。

用户要求贡献时，在工具仓库执行：

```sh
python community.py export --analysis data/private/analysis.json --dataset UNIQUE_STUDY_V1 --output contributions/UNIQUE_STUDY_V1.json
python community.py validate contributions/UNIQUE_STUDY_V1.json
python community.py merge contributions/UNIQUE_STUDY_V1.json --db exports/community.sqlite --html exports/community.html
```

ID 使用小写字母、数字、连字符，实际要求以 `docs/community-schema.md` 为准。导出器只保留契约允许的统计字段，不能通过改字段名塞入原文。来源链接公开可追溯帖子，不能称为匿名化；不附评论者 ID、主页、原文、年龄细格或登录资料。

先验证导出，再按用户授权提交到指定仓库。PR 附研究主题、排序、观察时段、采集完整度、核对规则和偏差。未获发布授权时交付本地统计包即可。

公共 SQLite 从已验证 JSON 重建；merge 需传全部希望包含的包。相同 dataset_id 内容不能静默覆盖，更新用新版本；跨包相同帖子保留观察批次，不把多个研究的 N/F 累加为独立人数。模拟与真实永远分离。CI 仅检查结构和约束，数据真实性仍需研究者说明与审查。

详见仓库 CONTRIBUTING.md、DATA-LICENSE.md。Skill 不需要知道历史使用者的私有路径、账号、真实评论或浏览器 ID。
