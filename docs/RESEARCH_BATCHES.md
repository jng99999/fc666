# 参数批次研究契约

入口：研究页“参数批次研究”，或POST /api/v1/research/batches。请求包含base（原RunRequest）和variants（strategy/parameters数组）。2..8组，参数先规范化再拒绝重复组合；未知策略、额外参数、非严格整数或非法窗口返回422，不创建任何成员。

base提供交易对、周期、历史窗口、固定截止、资金、费用、滑点、配置。一次prepare读取闭合历史和当前规则，将同一份值快照复制到每个独立任务；后续行情/规则变化不修改这些快照。EMA variant的period同步到其config.period；SMA保留base.config.period用于附带市场分类，fast/slow仅影响策略。所有成员具有相同截止时间和货币/容量假设。

使用同一PG advisory admission lock及单一事务写入所有成员。全局QUEUED/RUNNING上限20，容量不足整批429且不写入任何成员，单任务提交使用相同锁。未新增数据库表：不可变request JSON保存batch UUID、slot、shared_sha256；原快照SHA和请求SHA继续保护每项执行输入。shared_sha256绑定数据、规则、交易对、周期和截止；每个v3 run_id另绑定策略、参数及完整config。

GET /api/v1/research/batches/{UUID}按slot返回成员状态、计数及真实指标；只对SUCCEEDED提供metrics/analysis，其他项为null。ACTIVE表示至少一个任务尚未结束，COMPLETE只表示全体结束，不代表全体成功。可出现成功、失败、取消混合。成员保留现有进度、租约、3次恢复、停止/取消、完整结果导出与版本分派。

POST /api/v1/research/batches/{UUID}/cancel按稳定job UUID顺序锁定成员，只标记尚未结束项。QUEUED立即取消；RUNNING通过原checkpoint结束并丢弃输出；成功/失败项保留；重复取消幂等。未新增worker，现有研究进程顺序执行成员，不承诺slot执行顺序。

页面以localStorage记录最后接受的batch UUID，刷新轮询恢复。按输入顺序比较净收益、收益率、回撤、费用、Sharpe、完整交易数，未完成项显示缺值，不补零；Sharpe不可用保留原因。页面金额最多8位小数仅供显示，完整精度保留在提示和下载的完整导出。每项可独立下载并离线复现；页面明确样本内过拟合风险，不自动选“最佳策略”。

限制：无自动寻优、样本外验证、参数网格生成、多用户鉴权、批次列表/保留清理或POST幂等键。丢失提交响应不会自动重试；已提交成员仍在现有任务记录中。批次成员共用值快照但在数据库各自保存，最多8份/1000根。历史JSON batch查找尚无专门索引，需在真实规模增加后再优化。
