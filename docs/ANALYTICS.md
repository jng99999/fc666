# 回测分析与市场分类 v2

## 成本和完整交易
引擎spot-next-open-v2保持v1撮合语义：仅现货long/flat、下一open、prior-volume容量代理、IOC。新增fill realized_pnl、reference_open和slippage_cost。滑点成本=数量×不利方向(取整成交价−open)，包含tick取整；已反映成交价格，不能再从账本扣一次。
完整交易由首次BUY到库存归零的最后SELL组成；多次部分退出合并为一笔，净PnL包含进出费用。尚未完全平仓的交易不加入胜率、Profit Factor和expectancy；fill count与closed_trade_count分别显示。持有秒数为实际入场open到最后退出open。

## 风险收益
- initial cash→首柱收盘→逐柱收盘，simple equity returns；样本stdev ddof=1。
- 连续365天市场，year_seconds=31536000；bar_seconds使用输入实际周期；risk-free=0。
- Sharpe=mean/stdev×sqrt(periods/year)；Sortino使用所有柱的负收益平方均值根（包括非负柱的0），不是仅负样本stdev。
- CAGR=expm1(log(final/initial)×year_seconds/实际N柱时长)；超出有限计算范围返回null+reason。
- Calmar=CAGR/max drawdown；无回撤或无可用CAGR时null，不能填0。
- 胜率=盈利完整交易/全部完整交易（平价计入分母）；Profit Factor=sum positive PnL/abs(sum negative PnL)，没有亏损则null；期望为完整交易净PnL均值。
- exposure_fraction为收盘时持有库存的柱比例，固定频率时间代理，不是tick级持有时间。
短于一年的年化只用于描述，清单/界面明示不是收益预测。零样本/零分母给明确不可用原因；不输出NaN/Infinity。资金/交易PnL用Decimal60位；无量纲统计采用有限浮点。

## 市场状态 er-atr-v1
period个闭合价变化，ER=abs(last-first)/sum(abs(close变化))。零变化ER=0；ER<0.35为RANGE，其余按净变化方向TREND_UP/TREND_DOWN。需要period+1柱及成熟ATR，否则UNAVAILABLE。
volatility依据ATR/close：<0.005 LOW，>=0.02 HIGH，其余NORMAL。阈值是明确固定启发式，周期/窗口改变结果，不是训练模型、预测或已完成多周期regime。每个结果标注available_at，append future bars不能改写过去。

## 版本兼容
新输出版本v2，run_id包含analytics/regime版本与清单。legacy_v1保留原资金/成交/输出及冻结的EMA/策略语义；reproduce_backtest按版本分派。已验证上一轮真实v1导出仍可完整复现。v1/v2财务equity相同而run_id不同；支持未知版本时先显式新增验证，禁止悄悄用最新版重算旧结果。
