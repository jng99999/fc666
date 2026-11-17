# FC666 已确认 UI 实现与验收

## 设计依据与交付范围

按用户提供的 `FC666-Approved-UI-Source.zip` 中 E 桌面 / B 手机的可运行源码实施。已启动并实际查看源码预览；不渲染设备外框、状态栏、展示板标题。用户提示词提及的《分析工作台 · E × B》《策略工作台 · E × B》《账户与资产 · E × B》三张独立图片不在本次附件中，因此本次可核对基准是 ZIP 的运行结果，不能声称完成了对缺失图片的逐像素比对。

实现于现有 Next.js 16.3.8、React 19.2.4、Lightweight Charts 5.2.1 项目，未升级依赖、未导入另一套产品、未替换后台或真实账本。

## 关键实现位置

- `apps/web/app/approved-ui.css`、`components/workspace-nav.tsx`、`app/layout.tsx`：顶栏、独立 64px 桌面导航、手机五项浮动导航、安全区及公共视觉。
- `app/page.tsx`、`app/analysis/analysis-workspace.tsx`：共用真实数据的行情 / 分析布局；行情布局把解读放在主图下方，分析布局放在右侧。
- `app/analysis/analysis-chart.tsx`、`analysis-indicators.ts`、`analysis-dialog.tsx`、`analysis.css`：真实蜡烛 / 走势、主图和副图指标、编辑 / 隐藏 / 移除、依据与放大图表。
- `app/research/page.tsx`、`research.module.css`：策略列表、参数与成本、真实报告恢复、配置过期标记、完整报告及高级研究入口。
- `app/portfolio/page.tsx`、`portfolio.css`、`portfolio-history-chart.tsx`：模拟组合总览、持仓、资产构成、真实保存观察、金额隐藏、账户选择弹窗。
- `components/terminal.tsx`、`components/trading-chart.tsx`、`lib/chart-adapter.ts`：保留原有深度、逐笔成交与连接详情入口。

回放、黄金、系统状态和模拟账户原有流程继续使用，统一公共控件与外壳；它们没有被描述为另有三张定稿图的新页面。

## 数据与权限边界

界面不预填 ZIP 中的示例报价、虚构账户、示例收益或随机曲线。Bitget 现货以 USDT 标示；黄金保留独立 USD 入口，未假定兑换率或合并两种货币。规则解读明确标示 AI 未接入。隐藏金额仅改变显示，关键权限仍由现有服务端控制。

本机未配置 API_BASE_URL，直接访问本地 API 时会显示真实服务不可用状态。另用浏览器只读 GET 转发读取现有测试站真实行情、已完成回测及保存的场景观察；原始响应未经数据改写。所有非 GET 在该验证中拦截，未新建回测、创建 / 停止账户或保存新场景。人为注入的 503 只用于验证错误提示，不计为后台成功。

现有后端未提交修改保持原样。`apps/api/settings.py`、`core/storage/models.py`、`core/storage/schema.py` 的合并 diff SHA256 在本轮前后均为 `bbcca5cf805cee2af80fba60f43c58efd95e5a9af83d3e183b78c61ea01f93bf`。已有 PPO 草稿也未被清理或覆盖。

## 外部实现参考

保留 [Lightweight Charts](https://github.com/tradingview/lightweight-charts) 既有依赖及 TradingView 标识 / 链接。已核对 [Apache-2.0 许可](https://raw.githubusercontent.com/tradingview/lightweight-charts/master/LICENSE)。

[FreqUI](https://github.com/freqtrade/frequi)（[GPL-3.0](https://raw.githubusercontent.com/freqtrade/frequi/master/LICENSE)）和 [Ghostfolio](https://github.com/ghostfolio/ghostfolio)（[AGPL-3.0](https://raw.githubusercontent.com/ghostfolio/ghostfolio/main/LICENSE)）仅作流程参考，未复制其实现代码，未以它们替换已确认的视觉。

## 启动

在 `apps/web` 执行：

```sh
NEXT_PUBLIC_MARKET_PROVIDER=bitget npm run build
npm run start -- --port 3056
```

真实 API 连接仍沿用项目已有 API_BASE_URL 配置，不把密钥写入前端。

## 验收记录

2026-10-24 继续按可运行定稿逐项对齐，公共区域采用原始 64px 导航 / 22px 列间距，手机使用原始 62px 底栏。将旧业务样式移入低优先级 cascade layer，并移除直接污染新布局的三处强制规则。分析页布局、指标弹窗与图表色值 / 网格 / 字号直接按定稿迁移；修复 resize 后 RSI 标注跨卡。策略页恢复独立表现卡、信号参数卡与真实配置底部抽屉，并修复长历史曲线最小柱距导致的历史截断。显式搜索策略不被旧保存报告覆盖，旧报告仍标原策略。账户桌面总览由约465px 收至定稿约392px，保留真实数据和未估值状态。

当前本地正式构建验证：

- `NEXT_PUBLIC_MARKET_PROVIDER=bitget npm run build`：exit 0，所有页面构建完成。
- `npm run typecheck`：exit 0。
- `NEXT_PUBLIC_MARKET_PROVIDER=bitget node --experimental-strip-types --test tests/*.test.mjs app/analysis/analysis-format.test.mjs`：100 tests / 100 pass / 0 fail。
- Chrome 正式构建响应式检查：8 路由、72 组视口，0 横向溢出、0 页面脚本错误；320/375/390/430/768/1024/1440/1920 及844×390横屏。
- 分析页正式构建真实只读 GET 检查：25 项通过，包含同尺寸源稿几何、指标校验 / 隐藏 / 删除、图表切换、依据弹窗、缩放标签边界。
- 账户：29 项真实只读检查，以及12项明确故障注入检查通过；加载、空数据、503重试与缺失观察均不伪造余额。

实际截图、命令原始日志与浏览器 JSON 位于工作目录 `outputs/approved-ui-20261024/`；前一轮证据保留在 `outputs/approved-ui-20261023/`。浏览器为本机 Chrome，未声称已在真实 iPhone Safari 手工验收。发布目标仅现有 `jng56/fc666-test` 测试项目，候选检查后再分配测试域名；具体部署身份及公开站复核记录单独保存在本轮输出目录，不能用本地通过替代公开站验收。实盘继续关闭。
