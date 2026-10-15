# Collector V2 实施契约

独立入口：`python -m xhs_research.collector`，默认离线。Provider的probe/search/note/profile返回规范化字典；REST适配绑定xpzouying REST v1字段结构，结构变化报schema，不默默返回空数据。

RestProvider只连接配置中的回环HTTP服务、禁用系统代理、拒绝重定向。凭证文件仅在显式live初始化读取；短效定位引用仅在Provider内存，不能由帖子token合成用户token。缺定位引用需要源重新发现或人工正常访问。

持久化：研究库与调度库位于scope隔离目录。证据JSON先原子写入并计算内容哈希，然后导入研究库，最后ack并更新库存；恢复扫描未ack文件。OS服务锁释放随进程退出，第二个runner不能同时访问同一配置的服务。

N/K/F调用原core的口径；画像不根据默认性别整数推断。公开资料未披露时仍为unknown，failed visit不算已核查。观察时间不倒填；回放保留source_note_observed_at，而不是旧转换器的完成时间。

评论按原生ID累计合并，较短响应不删除旧评论；新正文使旧意图标注失效。原始资料追加快照；去凭证证据只在data/private。完整性保守partial，当前REST结构没有足够共同证据证明全层级完整。

配置和策略冻结在manifest：20候选/300provider调用/240主页访问/6深查/1800秒，任一上限停止；所有重试计入provider调用。初筛8人、深查总上限30，先选人后画像；同作者最多3篇深查，固定复核按seed+note_id哈希选，不重新抽取。

源码字段已核对（2026-10-14）：REST外层success.data.feed_id/data，内部note/comments.list；profile为userBasicInfo/interactions。安装版本尚未完成实机字段探针。整数gender不支持自动性别证据；扩展披露字段仅接受明确public_profile来源，标准服务若没有则产生人工核对缺口。

每个Provider操作返回records/source/observed_at/completeness/stop_reason/provider_version/next_cursor，以及方便处理的note/comments/profile或candidates字段。records只在操作结果内存里引用；落库保留所需的规范实体，避免重复存储整份返回体。capabilities.json保存实际探针结果，不把绑定协议版本当作安装版本已验证。

固定复核在深查容量中预留一席（存在被哈希选中的反例/未知时），未执行的复核与其他未选中案例明确deferred_quota；名单冻结。主题候选配额按容量分摊，避免第一个主题占满整批。当前源无搜索游标，继续扩样需用户配置新查询或源结果更新；重复批次只增加新快照，不能虚增独立帖子或用户。
