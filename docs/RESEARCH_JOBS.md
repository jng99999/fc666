# 持久研究任务合同 v1

状态：Phase6，支持内置EMA/SMA Long/Flat v1和Spot回测v3；旧v2任务按版本恢复。用户代码执行、完整SDK/Lab未实现。

提交接口POST /api/v1/research/jobs：参数验证后固定as_of，读取最多1000根真实finalized bars与instrument规则，保存请求/数据/版本快照及SHA256。只有连续且至少两根的数据可排队。最多20个QUEUED/RUNNING任务，以PostgreSQL advisory transaction lock保障并发提交限额。当前不提供Idempotency-Key，客户端不自动重试POST，重复明确提交会生成不同任务。

状态：QUEUED→RUNNING→SUCCEEDED/FAILED/CANCELLED。Queued取消立即终止；Running取消记cancel_requested，在每25柱/计算结束的checkpoint和最后提交处检查。先完成成功提交时取消返回409；先提交取消请求时不得存成功结果。Progress只在SUCCEEDED为100，其他状态最多99；重试从0开始并显示attempt。

领取：FOR UPDATE SKIP LOCKED，独立worker单任务执行。Lease30秒，checkpoint续租；不同worker UUID作fencing token，过期旧worker不可发布。死worker留下RUNNING任务，过期后重新领取同一快照，最多3次；取消中的死任务过期后转CANCELLED。正常计算校验失败直接FAILED，不反复重试。Worker shutdown不冒充取消或成功；结果整笔事务写入，不保存半成品为成功。

API：GET jobs（最近20条）、GET jobs/{uuid}、GET jobs/{uuid}/result（仅SUCCEEDED）、POST jobs/{uuid}/cancel、GET strategies（实际支持策略/参数schema）、GET status（worker heartbeat freshness）。后台队列使用PostgreSQL，Redis清空不丢任务。结果和任务记录当前保留，无自动删除用户研究数据；长期归档/配额待做。

页面提交后轮询状态；localStorage只保存active job UUID，刷新可恢复，最近任务可重新读取结果。网络失败显示错误并重试GET，未知任务404解除忙碌。导出为完整v3数据/规则/策略版本/参数/结果（旧v1/v2保持离线兼容），可纯离线复现。sync backtest旧接口保留作兼容/诊断，页面默认走后台任务。

迁移0003加jobs/workers；0004加K线修正审计。Startup启动API/web/research/market；工程schema readiness与research heartbeat/market freshness独立。当前单用户loopback开发环境，不支持公网多用户认证/授权、用户策略沙箱、完整审计事件流或真正分布式时间同步。

参数批次成员沿用本任务生命周期与容量锁，见RESEARCH_BATCHES.md；整批准入不代表整批计算成功，取消仅作用于未完成项。
