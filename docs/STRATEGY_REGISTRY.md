# 策略注册基础验收

本切片建立不可变内置策略注册表，按明确ID/version解析，未知版本拒绝；参数模型冻结并禁止额外字段，周期只接受整数2..500（拒绝bool、浮点和数字字符串）。现有EMA回测通过注册入口创建策略；指标准备仍由原回测引擎负责，策略参数period对应config.period。

新增SMA Long/Flat计算模块：fast < slow；满slow根后，fast均价严格大于slow均价则LONG，否则FLAT。以Decimal窗口和交叉乘法比较，避免除法舍入改变相等边界。时间必须有时区且严格递增，close必须有限正值；无效输入在改变状态前拒绝。调用方负责提供已闭合、连续、同品种的柱，策略本身没有交易接口。

SMA已注册但backtest_available=false，尚未接入撮合、后台任务和网页。公开研究catalog只返回可执行策略，仍只有EMA；新参数schema为增量字段，原config_schema保留。未变更引擎版本、历史manifest、job快照、数据库或前端。

验证：完整pytest 117 passed，1条既有Starlette/httpx弃用警告；独立Fraction均价参考、预热、相等边界、历史前缀一致、参数拒绝、时间/数值错误与状态保护通过；此前真实v1/v2导出逐字段离线复现通过。API/web/research/market四服务重启后ready，实际API返回版本化EMA和严格parameter_schema。前端未变更，本切片未重复浏览器构建和性能测试。

后续目标：先冻结v2复现/待执行任务分派，再加入v3策略与参数快照，接入SMA回测及页面选择；必须验证独立信号参考、下一柱成交、旧job恢复和完整导出复现，之后才将SMA标为可回测。本次信号参考验证不代表完整事件/向量撮合一致性验收。
