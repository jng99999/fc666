# 版本策略与参数

内置不可变注册表按strategy ID/version解析；未知版本拒绝。参数模型冻结、禁止额外字段；周期只接受整数2..500，拒绝bool、浮点与数字字符串。公开catalog返回EMA/SMA两种可执行策略及parameter_schema，保留原config_schema。

EMA：SMA种子EMA成熟后，闭合价严格高于EMA则LONG，否则FLAT。parameters.period必须等于config.period；不传parameters时沿用config.period，兼容旧请求。

SMA：parameters.fast < parameters.slow；满slow根后，fast均价严格大于slow均价则LONG，否则FLAT。Decimal窗口通过交叉乘法比较，避免除法舍入改变相等边界。时间有时区且严格递增，close有限正值；无效输入在状态改变前拒绝。回测引擎负责已闭合、连续、同品种校验。SMA的config.period仅控制附带市场分类/指标；网页明确标作市场分类周期，不影响SMA窗口。

两策略共用现货下一柱开盘撮合、Decimal现金/库存/费用、tick/step/minimum约束及上一闭合柱成交量容量代理，不产生真实订单。网页可选策略、填参数并提交后台任务；导出保留完整数据和manifest参数，刷新恢复结果时展示实际运行策略。

新运行使用spot-next-open-v3，manifest绑定strategy/version/规范化parameters/config、真实数据及规则；SHA运行ID随这些输入变化。后台request SHA与snapshot SHA校验仍保留，策略ID必须与请求一致。旧请求不含parameters仍可执行。保留legacy_v2账本与版本分派，已有v2排队任务按v2执行；v1/v2/v3导出按版本逐字段离线复现。旧版共享指标/分析模块目前未改变，未来改动这些公式仍须保留旧公式并复验历史导出。

本轮验证：120项后端测试通过，1条既有Starlette/httpx弃用警告；独立Fraction均价参考、预热/相等边界/历史前缀、参数拒绝、下一柱成交、v2旧排队任务执行、SMA任务参数与导出复现通过。迁移检查无变化，前端typecheck/生产build通过，既有真实v1/v2导出复现通过。浏览器结果见PHASE_6_STRATEGIES_REPORT.md。

完整SDK、用户代码沙箱、向量/事件完整撮合对照、优化与研究批次仍未实现；本次独立信号参考不能替代完整撮合一致性验收。下一目标为参数批次研究：明确数量上限、不可变共用数据快照、逐任务失败/取消语义和比较指标，再实现页面比较。
