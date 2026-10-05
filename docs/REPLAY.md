# 闭合柱历史回放契约

Phase7首片：BTCUSDT/ETHUSDT Spot六周期的固定闭合OHLCV历史回放。不存在历史逐笔成交/订单簿、柱内路径、回放撮合或模拟/实盘下单。回放页不挂载实时socket、不请求实时行情或完整市场历史接口。

POST /api/v1/replay/sessions一次捕获2..1000根已闭合、同品种/周期且连续的历史和当前规则；请求允许symbol/timeframe/limit/period/as_of，严格参数校验。快照version=closed-bar-replay-v1、数据、规则及实际截止共同进入SHA；PG replay_sessions保存快照与cursor/revision。schema0005新增该表，ready验证表存在与最新迁移。

初始cursor=0，clock=首柱open_time，没有价格/指标。cursor=k时只返回前k根，clock为第k根close_time；每根close_time <= clock。指标仅从这个前缀重算，未成熟值为null，确认pivot/突破按原可用时间返回，重置不保留后来的确认信息。响应允许事先知道快照时间范围、总柱数及hash，禁止返回未到达价格、完整dataset、完整snapshot或未来指标。没有完整快照下载端点。

GET /api/v1/replay/sessions/{UUID}返回已确认前缀。POST /.../{UUID}/command允许step(count=1..10)或reset(count=1)，必须提供expected_revision。事务FOR UPDATE后校验revision；过期版本409，不推进。游标实际变化才revision+1；结束时step幂等不变。reset回到0、清空指标前缀，快照保持不变。DB约束cursor在0..dataset长度，revision非负；hash/版本/数据完整性错误拒绝读取或推进且不写游标。

页面逐根与播放均只在服务器确认后显示新数据。播放每次请求一根，目标速度0.5/1/2/4根每秒，网络与计算耗时会降低实际速率，不跳帧、不宣称真实市场逐笔时间。暂停阻止后续请求，已发出的当前步仍可能完成。刷新恢复PG游标并默认暂停，localStorage保留最后会话UUID和速度。结束自动暂停。错误/版本冲突暂停，并GET同步服务器；不自动重发POST，丢失响应不会重复推进。若其他页面推进，当前页不主动订阅，但下次命令通过revision冲突同步。

图表使用既有ChartAdapter，只有响应前缀作为输入，Decimal->Number仅绘图。默认跟随窗口显示所有已到达柱，可关闭跟随后手动浏览；刷新表单恢复会话固定配置。页面明确最新已到达收盘与回放时钟；原SMA/EMA/Bollinger/VWAP及独立RSI/MACD/ATR pane、已确认结构可按前缀切换。改动表单只影响下一次创建，现有会话展示其已固定manifest。

测试：后端连续性/零根/每根时钟与因果指标/重置、未来价格变化不改相同前缀、并发旧revision只有一项成功、重复推进拒绝、真实PG新客户端恢复及后续历史/规则变化不改已接受快照、hash篡改回滚、DB游标约束通过。浏览器覆盖实际控制、暂停/刷新、冲突、提交后丢失确认响应无自动重试、结束、移动布局及不请求实时/完整历史数据。报告PHASE_7_REPORT.md。

范围限制：最多1000根，指标GET每次从前缀重算；没有持久播放后台时钟、会话列表/删除/保留策略、多用户鉴权、回放策略/账本、逐笔订单簿或自动交易。公开服务仍只绑定loopback；不把独立回放首片当作完整Market Replay平台。下一切片可在明确版本/事件语义后引入回放策略决策，仍不执行真实订单。
