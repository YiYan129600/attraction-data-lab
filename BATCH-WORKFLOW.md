# 增量批次采集

本工具只做离线规划、证据去重和已授权 CUA 会话内批量读取。没有启动浏览器、采集新样本或实测 token 降幅。既有 captures、core 和研究结论不改写。

## 离线用法

```powershell
python -X utf8 batch_workflow.py audit
python -X utf8 batch_workflow.py plan --cache-days 7 --control-quota 1
python -X utf8 batch_workflow.py checkpoint --task-id TASK_ID --state blocked
python -X utf8 batch_workflow.py checkpoint --task-id TASK_ID --state cancelled --reason "人工确认取消原因"
python -X utf8 batch_workflow.py record-batch --duration-seconds 20 --new-candidates 8 --new-notes 2 --profile-visits 6 --cache-hits 4 --status completed
```

私有目录 data/private/batch-workflow 保存 queue.json、cache-seed.json、summary.json、cost-log.jsonl。摘要只有计数；队列/种子含原生 ID，不公开。缺失 tokens 为 null，duration 不是费用。默认六主题 body_display/fitness/outdoor/daily/car/hobby；daily_life、fitness_progress 等归一化，未知 query 不成为新主题。可重复 --theme 指定。空 study 也生成轮转搜索任务。候选先复用作者证据，未知作者排 author_probe，已核实门槛失败不继续排详情；已有作者但粉丝非exact/性别unknown或缺作者ID留manual_review，只有exact≤5000且male才排note_probe。

精确粉丝0–5000、明确男性、评论≥50才进入画像；rounded/未知留 manual_review，明确非男性/超粉丝门槛/低评论单列 excluded。首八人是实际 DOM 顺序不同外部一级用户。明确未成年移出成人目标 N/K/F，成人 N不足8留 unknown，不无限补人。实际核对 N≥8、K/N≥60% 后，F/K>50% 是 positive，F/K≤50% 是 counterexample；披露不足为 unknown。

有深查证据时，按最多前30位已核查用户重新分类，不永久沿用首八人。30位已查含一位未成年即成人 N29，不无限补30。反例每主题默认仅一篇新深查，优先匹配正例粉丝档；其余标 deferred_quota。已完成30人的反例显示 completed_evidence/done，不重复 screen。反例 cohort 不混入正例推荐统计。理由只描述已检查样本全部明确男性、未观察到明确女性或男性占优；仅男性数大于女性数使用male_heavy，平衡或其他组合标balanced_or_other；未核实完整池不得宣称整帖无女性。

缓存每ID先选最新记录，reviewed=true且默认七天内才复用；未知也缓存，最新失败使旧成功失效。重建队列保留未完成任务，真实新证据覆盖的旧 note_probe/author_probe/profile/comment_expand不再保留。同用户访问去重但保留 dependent_note_ids。done不能代替证据，缺证据/缓存过期恢复pending；checkpoint可改回pending，cancelled必须写原因。

## CUA persistent REPL

一次定义 cua_batch_helpers.js 源码，再 var batchHelpers = createBatchHelpers()，同会话持续复用。profiles(profileTab,requests)接当前已授权 tab handle和实际页面链接，requests为1–12项{id,url}，通常6–12，尾批无需凑数。签名URL仅内存，不猜、不打印、不落盘。每批仅返回访问/命中/失败/停止计数。readProfile核对实际URL用户ID，失败不算成功；登录/验证码/403/无访问权限/异常停止批次，人工检查，不换身份或绕过限制。

跨会话由授权文件工具读私有cache-seed.json后传 seedCache(rows)，无简介全文；JS不读文件/网络，这一步仍需人工或授权工具搬运。

readNote(tab,noteId,creatorId,prior)只做增量，必须传匹配的既有完整捕获，首次完整采集沿用 [采集接入](skills/attraction-audience-research/references/collection.md) 字段契约。支持 /explore/ 和 /search_result/，作者用 #noteContainer :scope > .author a.name核对，身份不符拒绝。prior的type/tags/media保留；评论按原生ID合并，剥离comment-前缀，保留此前加载评论与人工intents/interaction_target/intent_annotation；正文未变保留人工标注；正文变化将旧标注移入previous_annotation，清空活跃intents/interaction_target/intent_annotation并标annotation_review_required待复核，避免旧意图被继续统计。导航链接必须是https，不自动推断intents。

实际评论主页链接只在内存，requests(ids)返回已看到链接，禁止打印请求。profileQueue(note,false)取前8人，true取前30人，延伸由队列/执行者按证据决定。滚动先看新截图再用CUA原生操作，不硬编码坐标。当前选择器未经站点重新实测，变化时按采集接入文档核实。

delta()取未确认保存记录，填既有本地证据桥表单；看到保存成功后才ack(delta)，失败保持pending。桥保留Origin/CSRF/8MB/凭证检查，之后whole-record canonical hash去重，重启扫描已有记录。同ID新时间/标注保留；整批重复成功且不写空文件。线程锁防双写，临时文件原子replace防部分capture暴露，不删除旧文件、不增加外部传输。

## 验证

python -m unittest discover -s tests -p test_batch_workflow.py -v

node tests/test_cua_batch_helpers.js

这些仅是离线与fake adapter关键路径验证，不证明浏览器当前性能或token降幅。
