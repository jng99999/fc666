# UI 设计系统与终端交互

当前实现说明：Phase 1 已实现基础 tokens、响应式工程入口与真实服务状态；专业 Terminal/chart/resize/performance 仍为后续设计。

状态：设计；不是可运行 UI。Dark Mode First，信息密度高、低视觉噪声、快速响应；避免巨大卡片、渐变与无意义动画。

## Tokens
| Token | 值/使用 |
|---|---|
| background / panel / elevated | #0B1018 / #111925 / #182334 |
| border | #2A3B50，分隔与输入边界 |
| text / secondary | #E6EDF6 / #A9B8CC |
| accent / focus | #77B7FF，可见 2px focus ring |
| buy / sell | #35D0A0 / #FF7785，始终加文字/符号，不只依赖颜色 |
| warning / halt | #F4C56A / #FF7785 |
| typography | 系统 sans，数值 monospace + tabular-nums；12/14/16/20px，正文 14px |
| spacing | 4/8/12/16/24/32px |
| radius | 2/4/6px，面板 4px |
| shadow | 仅 overlay，0 8px 24px rgba(0,0,0,.35) |
| z-index | base 0、sticky 10、popover 30、modal 50、toast 60 |
| chart | 背景同 panel；网格 border 低透明；涨跌同 buy/sell；volume 降透明；指标配独立色与图例 |

实现前验证 WCAG AA 正文 4.5:1、非文本控件 3:1；不能仅凭色值宣称可访问性通过。颜色可配置，涨跌颜色需同步 chart/orderbook/position。

## 桌面线框
```text
+--------------------------------------------------------------------+
| FC666 | Symbol | Price/24h | Funding/OI or N/A | Mode | Account      |
+------------+-------------------------------------+-----------------+
| Watchlist  | Chart toolbar: timeframe/indicators  | OrderBook       |
| Market     |                                     | Recent Trades   |
| AI summary | Candlestick + Volume + overlays     | Order Form      |
+------------+-------------------------------------+-----------------+
| Positions | Open Orders | History | Trades | Strategy | AI | Logs   |
+--------------------------------------------------------------------+
| Connection | Data age | Strategy | Risk | Execution | Trading Mode  |
+--------------------------------------------------------------------+
```
1366px 初始布局左 200px、右 300px、中央 flex，底部 240px；支持 keyboard 可操作的 resizable separator、最小宽度、隐藏面板、图表全屏及布局版本化持久化。小于 1024px 将右侧改 tabs；小于 768px 单面板切换，不缩成不可读桌面。移动端优先监控，不自动开放 Live 下单。

## 共享组件与状态
StatusBar、InstrumentSelector、NumberCell、DataTable、Panel、Tabs、OrderForm、ChartHost、JobProgress、ErrorBanner、ConfirmDialog、EmptyState。统一 loading/empty/error/stale/disconnected/not-implemented；行情为空显示无数据，绝不填样例价格。模式固定可见；PAPER 为模拟账户，LIVE 为独立权限及强提示。
订单表单显示 quantity/notional/fee estimate/risk reason；submission 与 exchange acknowledgement 区分。按钮 pending 时防双击，仍需服务端幂等。平仓另行确认，Kill Switch 始终可达。
Terminal chart 适配接口提供 setHistory/updateBar/setOverlays/resize/dispose，业务不依赖 Lightweight Charts 类。保留 TradingView NOTICE 与归属链接；不是把其终端 UI 复制过来。
策略实验室筛选项及交易详情见 [PRODUCT](PRODUCT.md)、[BACKTEST](BACKTEST.md)。图表点选成交联动列表并保留 as_of 指标。AI 卡片显示时间/来源/不可用原因。

## 实时更新与性能
WS -> parser/worker -> normalized store -> per instrument/channel selector -> 局部视图。Chart 用 imperative update，orderbook 100ms 显示批处理，trades 有界 ring buffer，长列表虚拟化。显示合并不得改变策略输入或丢 book delta。React 页面框架不订阅所有 tick。
预算：交互尽量 60 FPS；FC666 内部行情到 UI p95 <100ms；普通本地 API p95 <200ms；100k candles 操作无持续主线程冻结；研究任务不阻塞 API。
Phase 3 在固定硬件/浏览器、两币种 burst replay、100k bars、10分钟负载下报告 p50/p95/p99、帧时间、long tasks、内存与 React commits；区分 exchange 网络耗时与 FC666 内部耗时。预算是验收目标，尚无实测。
TradingView/Binance/OKX/Bybit 的桌面交互、截图与键盘行为比较仍待 Phase 0 研究补充，当前线框是本项目设计推导，不宣称逐一实测竞品。
