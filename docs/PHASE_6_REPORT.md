# Phase6 后台研究首片报告

日期：2026-10-05（Asia/Shanghai）。状态PARTIAL：持久任务/固定策略版本/状态恢复交付，完整Strategy SDK/Lab、用户策略执行和扩展撮合尚未完成。

## 实现与验收
PostgreSQL持久任务、immutable request/data/instrument/version快照、并发20限额、SKIP_LOCKED领取、30秒可续租/旧worker fencing、最多3次恢复、进度/取消/失败、原子结果。独立研究worker加入受控start/stop；网页异步提交、轮询、取消、刷新恢复、最近任务和完整导出。固定内置策略catalog给出版本和真实参数schema。
106项后端测试通过；迁移upgrade/downgrade/re-upgrade与alembic check、类型检查、production build通过。新增真实PG测试覆盖并发领取、lease reclaim/旧token拒绝、计算中取消、取消/成功提交竞争、队列上限、请求/快照篡改、retry上限、shutdown、market/rule后续变更不改变已排队快照，以及接口参数/结果状态。
真实浏览器验证：暂停研究worker后任务可排队/取消且无结果；第二任务跨网页刷新和真实worker重启完成；完成结果刷新后恢复并离线完整复现。既有研究与终端/移动端/market故障恢复回归通过，无页面JS错误。短时终端298个book样本P95 54ms、frame P95 16.7ms，无观测long tasks；不宣称生产/长时load。
证据：.runtime/jobs-browser-report.json、browser-job-export.json、research-jobs.png；源测试tests/test_research_jobs.py与tests/browser_jobs.py。

## 启动时发现并修复的数据问题
真实ETH 2026-10-05T16:13:00Z分钟柱，原WS最终close2697.89/volume11425.5472，官方REST后续close2697.88/volume11427.3515，触发原有DataConflict而阻止重连。
新增显式REST修正路径：两次匹配官方REST数据、闭合至少60秒，仅在backfill遇到冲突时启用；同一事务记录previous/revised/原因/时间后更新规范历史。不同确认、过新数据或批内任一不确认则整笔拒绝/回滚。普通save_candles继续拒绝覆盖，不删除原值审计，既有导出/已提交job快照不变。开发库真实修正已留下审计并恢复startup；不冒称从未有冲突。

## 下一切片
版本化策略扩展合同与任务持久事件/归档、更多撮合语义、独立事件/向量对照。未实现user-defined Python执行、沙箱、限价/止损、衍生品、Paper/完整Risk/OMS/Live；实盘不因后台任务健康而解锁。
