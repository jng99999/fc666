# Mobile exchange UI

Scope: mobile only. Desktop retains the deployed `26546bf` layout, navigation, chart rendering and paper-page descriptions. Shared exchange skins are enclosed in the same phone media query (small viewport or touch/coarse device up to 1024px). New market statistics and route tabs are hidden outside that query; chart channel improvements use the same mobile gate.

## Reference review

Reviewed the actual public Bitget mobile trading guide and its linked app screenshots, and the OKX chart-layout/settings guides:

- https://www.bitget.com/support/articles/12560603820580
- https://www.okx.com/en-gb/help/how-do-i-use-the-chart-trading-layout
- https://www.okx.com/help/trading-settings-faq

Applied their information hierarchy to existing functionality: product selection and price summary, compact period/indicator controls, dominant price chart, secondary evidence panels, and persistent navigation. The mobile shell uses a white background, thin dividers, small radii and compact forms. Market summary labels distinguish the actual 20-bar high/low from the live 24-hour volume; unavailable provider fields are not relabelled as 24-hour statistics.

Directional mobile account descriptions are collapsible; the news-state summary and missing-source warning remain visible. Account selection, entry/exit commands, stops, cost assumptions, authentication and server-side news/risk gates are unchanged. Desktop keeps the original expanded descriptions and headings.

## Direct landscape

Opening the phone chart switches its logical layout to landscape immediately. In a portrait viewport, its frame rotates clockwise 90 degrees and swaps width/height; natural device landscape uses the unrotated wide frame. This rotates page content and does not rely on an OS orientation-lock permission.

Drawing anchors are mapped from physical screen axes back to chart pixels. Rotated-mode gestures pan logical time and zoom around the selected local anchor; native chart gestures remain in natural landscape and desktop. Portalled drawing/indicator dialogs follow the chart orientation. Drawing-tool navigation scrolls the chart body while retaining its close header.

## Trend channels

Mobile automatic channels retain their confirmed formation anchors, project those fixed lines only through the observed object lifetime, and show both boundaries plus a dashed midpoint. Up/down selectors retain the latest valid channel of each direction. Broken channels stop at the recorded break. The chart never manufactures a channel when confirmation is absent; manual three-anchor parallel channels remain available.

Core price-action rules, execution signals and ledgers are unchanged. Channel projection is a rendering change, not an out-of-sample profitability result. Validation evidence belongs to the final source version and release receipts, not prior commits.
