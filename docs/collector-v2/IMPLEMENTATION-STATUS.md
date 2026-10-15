# Collector V2 实施与恢复状态

本页记录 **2026-10-14 的历史实施与离线验证证据**，不是当前服务状态探针。该日P0–P4已实现并验证；P5真实站点试跑受服务不可连接限制，不能报告网站提速或新增真实样本。当时未commit/push。2026-10-15 本轮整理发布采集代码、方法文档与严格聚合统计；没有重新运行真实采集或实机字段探针。最终推送及CI结果以Git记录和Actions为准。

## 交付与验证

- xhs_research/collector/：配置、离线CLI、捕获回放、REST适配、scope隔离状态库、单服务OS锁、证据/导入/ack恢复、预算、缓存、固定8/30人抽样、反例与审计配额、台账和报告。
- templates/collector-config.json、collector_demo.py、显式synthetic示例、23项新增测试。
- CONTRACT.md、USAGE.md及README/INSTALL/Skill接入；CI新增离线示例步骤尚未推送，不能声称远端新版CI已执行。
- 全部90项Python测试通过，原Node helper测试通过。报告真实渲染确认20候选完整；文档链接、公共白名单、层级计数与私有资料未跟踪检查通过。

## 运行证据

回放批次在本地私有采集目录保存；scope与文件不属于公开发布数据。

| 批次 | 已核实结果 |
|---|---|
| cold-verified | 20候选，19篇有详情、1篇在搜索计数层排除；782条唯一评论；13初筛、6深查；246次离线操作、49缓存命中；数据库valid=true |
| warm-verified | 36次离线操作、259缓存命中；仍13初筛、6深查；新增唯一帖子/评论/作者均0 |
| final-warm-verified | 缓存delta写入改造后的回放：36次离线操作、259命中；全部20候选及19详情进入报告，层级计数一致，公共包有效 |
| collector-demo最新批次 | 两篇完全虚构案例，2初筛/2深查；展示初筛正例转为深查反例；暖缓存6次操作，没有网络 |
| live-probe-1（另一live scope） | 1次实际本地服务调用，stop_reason=service_unavailable；0主页访问/0候选/0新增实采；失败摘要与空研究报告保留 |

冷缓存循环10.156秒、warm循环8.640秒，均为本地回放，不含Provider初始化或报告生成，不证明网站速度。246→36仅证明缓存减少读取动作，不换算成费用或站点提速倍数。token缺少可核验来源，始终null；collector自身无模型调用。

初期archive-check-1按输入行计算new_unique存在重复，已经改为原生ID集合，并以cold-verified的782条和研究库一致性验证替代。该早期批次不作为唯一数量结论，证据仍保留。

## 完成审计

| 规格项 | 当前权威证据 |
|---|---|
| P0配置、Provider、异常与能力 | CONTRACT、config.py、每个结果的records/source/time/completeness/version等元数据、capabilities.json；安装版本验证明确false |
| P1回放与落库闭环 | 真实20候选回放、valid=true、公共包校验、一键synthetic示例；原研究目录只读 |
| P2抽样、缓存与分组 | 测试覆盖8/30冻结、未成年不补位、TTL/失败/未来时间、跨帖缓存、分组反转、固定审计与配额；实际分组迁移字段保留 |
| 计数与研究口径 | core唯一计算N/K/F；原生ID计数、较短响应不删除、层级未知单列、作者回复与作者总评论分开；社区白名单不变 |
| 恢复与单写入 | 写文件后/导入后中断恢复幂等；批次按run隔离；OS锁跨研究和端口别名，进程退出释放 |
| 默认离线与预算 | doctor/plan默认0网络且不读凭证；REST无--live拒绝；请求/画像/时间上限和恢复不重置测试；失败和预算有独立退出码 |
| P3 REST适配 | 源码结构绑定、本地假HTTP服务真实测试搜索/详情/重定向；401/403/429/登录/验证码/5xx分类；只读路由；用户定位不伪造。实机字段探针受阻 |
| P4报告与文档 | 20候选HTML渲染、19详情聚合表，保留反例/未知；USAGE、INSTALL、Skill；新深查基线缺失时增量指标null |
| P5条件性试跑 | 连接失败与恢复条件已交付；正常服务/登录条件尚不成立，20候选站点实测未执行，不用mock替代 |
| 私人数据与发布 | 数据、配置、证据和台账都在gitignored私有区；无新增commit/push；公共fixture明确synthetic |

## 真实服务与恢复条件

2026-10-14 的历史检查：已安装服务二进制，但配置的回环端口不可连接；沙箱外探针也失败，未观察到端口监听。未确认正常登录。这不是2026-10-15的实时状态结论。未重新安装或启动浏览器，未复制Cookie，原运行目录只读。

用户已有服务正常启动并登录后，先执行doctor --live，再resume live-probe-1 --live。预算继续累积。运行中任务若被访问限制阻断，只有外部条件改变后才显式--retry-blocked。命令与配置见USAGE.md。

已在2026-10-14核查上游源码：详情外层为success.data.feed_id/data，内部note/comments；用户资料为userBasicInfo/interactions。标准User不返回用户签名，主页接口却要求xsec_token；整数gender不保留披露是否存在。只使用真实来源的用户定位引用，不借帖子签名构造；无明确性别/年龄证据时unknown或人工核对。API字段缺口下可交付帖子评论链路与核对队列，不能宣称自动画像完成。

参考：https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/types.go 、 https://github.com/xpzouying/xiaohongshu-mcp/blob/main/types.go 、 https://github.com/xpzouying/xiaohongshu-mcp/blob/main/service.go 。安装版本尚未实机验证等同当前main。

本次已覆盖可执行的开发范围与外部阻塞证据。后续实机兼容、吞吐和新增样本必须在服务恢复后验证。
