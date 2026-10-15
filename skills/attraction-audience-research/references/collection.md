# 采集接入与证据保存

本 Skill 的统一输入是研究 envelope，不绑定某个爬虫。先检查现有工具与登录状态，用一篇样本验证“搜索→详情→作者→评论→导入”能否完成，再扩样。不要重新浏览多个第三方仓库或长时间停留在安装。

已有Collector V2时优先用 `python -m xhs_research.collector` 批量执行，配置与命令见仓库 `docs/collector-v2/USAGE.md`。先doctor/plan离线核查，再在已有读取授权下显式--live；报告与缓存均在私有run目录。回放不算新实采，源码适配和mock通过不证明本机服务可用。缺少用户签名/性别披露时保留人工核对队列，不猜字段。

## 读取路径

1. 已有授权的 MCP/API 能返回所需字段时，优先批量读取。历史试点参考 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)，但本仓库不捆绑它，也不承诺其当前接口兼容。核对当前工具 schema，不硬编码历史版本参数。只调用研究需要的读取能力。
2. 现有 MCP 不可用时，用用户已授权的浏览器会话查看公开页面。`cua_batch_helpers.js` 是可选的 Codex CUA 辅助，仅在环境支持的工具中加载；其他环境可按字段契约编写适配器。选择器变化需小样本核对，不无限重试。
3. 无自动读取能力时，手工填写 `templates/empty-study.json`，或导入用户已授权的资料。离线分析不需要登录。

服务的“已启动”“已登录”不证明采集成功。Cookie、签名 URL、令牌仅用于既有授权会话，不打印或写进研究数据。对研究无关兴趣的浏览会影响后续推荐，应使用用户明确安排的独立会话；不能承诺标签搜索能“修好推流”。

## DOM 捕获契约

本仓库 `import_browser_captures.py` 支持以下记录，每条包含 kind 与带时区 observed_at：

| kind | 关键字段 |
|---|---|
| search_dom | query、sort、candidate_count、candidates、failure_reason |
| creator_dom | creator_id、stats[{label,raw}]、gender_icon；粉丝按标签读取 |
| note_dom | note_id、creator_id、title、desc、tags、comments_raw、comment_header、comments、media、end_visible、expand_buttons |
| audience_dom | user_id、reviewed、gender_evidence、tags_text、audience_annotations |
| visual_annotation | note_id、media_index、subject、composition、scene、light、evidence、version、reviewer |
| access_probe | status、failure_reason；记录真实访问失败 |

comments 保留平台原生 id、user_id、level、parent_id、is_author、content、date_raw；不由昵称或文本生成伪用户 ID。浏览器原始资料仅放本地私有区。公开资料明确的性别图标才可作 gender_evidence；API 缺失字段解码得到 0 不能当性别证据。

采集与人工解释分离：头像、名字不推断性别；年龄只记录明确公开年龄。作者标签必须人工确认指向作者本人，填 `author_self_report_reviewed=true` 后才作为公开自述；普通含“男孩”的 tag 不自动证明作者性别。

## 保存与导入

`python browser_capture_bridge.py` 在本机回环地址提供证据保存表单。不要改为公网监听。在支持的浏览器中把无凭证 JSON 数组填入表单，看到成功后再确认批次；也可由授权文件工具直接追加 `data/private/browser-captures/capture-*.json`。

```sh
python import_browser_captures.py --study MY_STUDY --owner LOCAL_OWNER
python batch_workflow.py audit
python batch_workflow.py plan
```

桥接器与 helper 的细节见仓库 `BATCH-WORKFLOW.md`。首次完整帖子读取需按上述字段从实际可见 DOM 保存；helper 的 readNote 用于持有既有完整捕获后的增量，不是独立爬虫。

结束文字不证明完整。平台精确总数、已捕获去重评论、末尾状态与回复展开必须共同满足完整性条件；否则 coverage=partial，并记录排序、加载数量和停止原因。原始证据追加新快照，不覆盖旧文件。
