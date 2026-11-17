# Mobile exchange UI

Scope: mobile only. Desktop retains the deployed `26546bf` layout, navigation, chart rendering and paper-page descriptions. Shared exchange skins are enclosed in the same phone media query (small viewport or touch/coarse device up to 1024px). New market statistics and route tabs are hidden outside that query; chart channel improvements use the same mobile gate.

## Reference review

Reviewed the actual public Bitget mobile trading guide and its linked app screenshots, and the OKX chart-layout/settings guides:

- https://www.bitget.com/support/articles/12560603820580
- https://www.okx.com/en-gb/help/how-do-i-use-the-chart-trading-layout
- https://www.okx.com/help/trading-settings-faq

Applied their information hierarchy to existing functionality: product selection and price summary, compact period/indicator controls, dominant price chart, secondary evidence panels, and persistent navigation. The mobile shell uses a white background, thin dividers, small radii and compact forms. Market summary labels distinguish the actual 20-bar high/low from the live 24-hour volume; unavailable provider fields are not relabelled as 24-hour statistics.

The follow-up Bitget pass removes the separate FC666 header from the narrow-screen quote page, retains a real settings/API link next to product selection, combines period scrolling/custom periods/landscape/indicator controls into one row, and makes the chart the dominant first-screen surface. Cyan/pink candles are mobile-only; changing back to desktop restores the existing green/red palette. Shared phone controls use neutral gray secondary controls, black primary actions, flat white sections and thin separators; destructive actions keep their warning treatment. Desktop selectors, layout and palette remain intact.

Directional mobile account descriptions are collapsible; the news-state summary and missing-source warning remain visible. Account selection, entry/exit commands, stops, cost assumptions, authentication and server-side news/risk gates are unchanged. Desktop keeps the original expanded descriptions and headings.

## Direct landscape

Opening the phone chart switches its logical layout to landscape immediately. In a portrait viewport, its frame rotates clockwise 90 degrees and swaps width/height; natural device landscape uses the unrotated wide frame. This rotates page content and does not rely on an OS orientation-lock permission.

Drawing anchors are mapped from physical screen axes back to chart pixels. Rotated-mode gestures pan logical time and zoom around the selected local anchor; native chart gestures remain in natural landscape and desktop. Portalled drawing/indicator dialogs follow the chart orientation. Drawing-tool navigation scrolls the chart body while retaining its close header.

## Trend channels

Mobile automatic channels retain their confirmed formation anchors, project those fixed lines only through the observed object lifetime, and show both boundaries plus a dashed midpoint. Up/down selectors retain the latest valid channel of each direction. Broken channels stop at the recorded break. The chart never manufactures a channel when confirmation is absent; manual three-anchor parallel channels remain available.

Core price-action rules, execution signals and ledgers are unchanged. Channel projection is a rendering change, not an out-of-sample profitability result. Validation evidence belongs to the final source version and release receipts, not prior commits.

## Double-top / double-bottom conditional analysis (mobile only)

The screenshot supplied by the user combines a descending channel, an M-shaped double top, neckline break/retest and hypothetical downside targets. The red future route is a scenario, not observed candles. The new mobile `形态与条件推演` panel marks observed pivots/neckline and a height-based target, with candidate, close-confirmed, retested, failed, reached, ambiguous and gap-expired states. It does not copy screenshot prices or route a signal to paper/live execution.

Reference: https://www.tradingview.com/support/solutions/43000653211-chart-pattern-double-top/ . FC666 uses its existing causal 2/2 confirmed pivots, not TradingView's 5/5 algorithm. Explicit research thresholds: peak/trough deviation at most 10% of pattern height; height above 0.3% of neckline; 4–80 bars between peaks/troughs; an approach of at least half the height in the preceding ten bars. These thresholds are a research definition, not calibrated profitability claims. Formation geometry and tolerance remain fixed; gaps are never bridged. Target/invalidation touching in the same later candle is marked ambiguous; the closing-confirmation candle cannot earn an intrabar target.

Browser diagnosis found the native chart canvases (z-index 2) above the rotated gesture plane (z-index 1). Isolating the chart host's stacking context only inside the rotated phone frame restores pointer routing while preserving the SVG drawing layer and all desktop layering. Ego pointer drag and wheel zoom are separately verified; raw CDP touch injection did not deliver pointer events, so it cannot establish real-device two-finger acceptance.
