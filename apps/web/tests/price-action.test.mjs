import test from 'node:test';
import assert from 'node:assert/strict';
import {analyzePriceAction, validatePriceBars, PRICE_ACTION_RULE_VERSION} from '../lib/price-action.ts';

const HOUR = 3_600_000;
const START = Date.UTC(2025, 0, 6);
const context = {instrumentId: 'BTCUSDT', source: 'bitget', timeframe: '1h', volumeKind: 'trade'};
function candle(index, close = 100, overrides = {}) {
  return {time: START + index * HOUR, closedAt: START + (index + 1) * HOUR,
    open: close, high: close + 1, low: close - 1, close, volume: 10, ...overrides};
}
const oscillation = (count) => Array.from({length: count}, (_, i) => candle(i, 100 + Math.sin(i * Math.PI / 3) * 2));
const analyze = (bars, extra = {}) => analyzePriceAction(bars, {...context, ...extra});
const geometry = ({kind, low, high, start, confirmedAt, direction, points, rule}) => ({kind, low, high, start, confirmedAt, direction, points, rule});

test('empty and short histories expose unavailable states without invented values', () => {
  const result = analyze([]);
  assert.equal(result.regime, 'UNAVAILABLE');
  assert.equal(result.last, null);
  assert.equal(result.confirmedThrough, null);
  assert.equal(result.atr, null);
  assert.equal(result.volumeProfile.kind, 'UNAVAILABLE');
  assert.match(PRICE_ACTION_RULE_VERSION, /v1$/);
  assert.equal(analyze([candle(0)]).atr, null);
});

test('invalid prices, duration, volume, duplicate and overlapping bars are rejected', () => {
  for (const bad of [
    {close: NaN}, {low: 0}, {high: 99}, {low: 102}, {volume: -1},
    {closedAt: START}, {closedAt: START + HOUR - 1}, {time: Infinity},
  ]) assert.throws(() => analyze([candle(0, 100, bad)]));
  assert.throws(() => analyze([candle(0), candle(0)]), /重复、乱序或重叠/);
  assert.throws(() => analyze([candle(1), candle(0)]), /重复、乱序或重叠/);
  assert.throws(() => analyze([candle(0), candle(1, 100, {time: START + HOUR - 1})]));
  assert.throws(() => analyze([], {source: ''}));
  assert.throws(() => validatePriceBars(Array(5001).fill(candle(0)), context));
});

test('a strict pivot appears only after two right-hand bars have closed', () => {
  const bars = [100, 101, 110, 101, 100].map((p, i) => candle(i, p));
  assert.equal(analyze(bars.slice(0, 4)).annotations.filter(a => a.kind === 'resistance').length, 0);
  const pivot = analyze(bars).annotations.find(a => a.kind === 'resistance');
  assert.ok(pivot);
  assert.equal(pivot.start, bars[2].time);
  assert.equal(pivot.confirmedAt, bars[4].closedAt);
  assert.equal(pivot.state, 'ACTIVE');
  const tied = bars.map(b => ({...b})); tied[3].high = tied[2].high;
  assert.equal(analyze(tied).annotations.filter(a => a.kind === 'resistance').length, 0);
});

test('future prefixes preserve annotation identity, formation geometry and causal confirmation', () => {
  const bars = Array.from({length: 95}, (_, i) => candle(i, 100 + Math.sin(i * .8) * 4 + i * .07));
  const previous = new Map();
  for (let length = 1; length <= bars.length; length++) {
    const result = analyze(bars.slice(0, length));
    const current = new Map(result.annotations.map(a => [a.id, a]));
    for (const item of result.annotations) {
      assert.ok(item.confirmedAt <= bars[length - 1].closedAt);
      assert.ok(item.start <= item.confirmedAt);
      assert.ok(item.end <= bars[length - 1].time);
      assert.ok(item.explanation.length > 20);
    }
    for (const [id, item] of previous) {
      assert.ok(current.has(id), 'future bars must not remove past research events');
      assert.deepEqual(geometry(current.get(id)), geometry(item));
    }
    for (const [id, item] of current) previous.set(id, item);
  }
});

test('BOS and CHoCH require closes through already confirmed pivots', () => {
  const values = [100, 102, 110, 102, 100, 98, 90, 98, 100, 112, 100, 88];
  const bars = values.map((p, i) => candle(i, p));
  const prefix = analyze(bars.slice(0, 9));
  assert.equal(prefix.annotations.filter(a => a.kind === 'bos').length, 0);
  const up = analyze(bars.slice(0, 10)).annotations.find(a => a.kind === 'bos');
  assert.equal(up?.direction, 'up');
  const down = analyze(bars).annotations.find(a => a.kind === 'choch');
  assert.equal(down?.direction, 'down');
  assert.equal(down?.confirmedAt, bars[11].closedAt);
  const wickOnly = bars.slice(0, 9).concat(candle(9, 105, {high: 115}));
  assert.equal(analyze(wickOnly).annotations.filter(a => a.kind === 'bos').length, 0);
});

test('FVG is third-close confirmed, cannot self-fill, and records later mitigation', () => {
  const bars = Array.from({length: 16}, (_, i) => candle(i));
  bars.push(candle(16, 103), candle(17, 105));
  const first = analyze(bars).annotations.find(a => a.kind === 'fvg' && a.start === bars[15].time);
  assert.ok(first);
  assert.equal(first.state, 'ACTIVE');
  assert.equal(first.confirmedAt, bars[17].closedAt);
  assert.equal(analyze(bars.slice(0, -1)).annotations.some(a => a.id === first.id), false);
  bars.push(candle(18, 104, {low: 102}));
  const partial = analyze(bars).annotations.find(a => a.id === first.id);
  assert.equal(partial.state, 'TOUCHED');
  bars.push(candle(19, 103, {low: 100}));
  assert.equal(analyze(bars).annotations.find(a => a.id === first.id).state, 'MITIGATED');
});

test('range box retains formation boundaries through break and return', () => {
  const bars = oscillation(24);
  const box = analyze(bars).annotations.find(a => a.kind === 'range');
  assert.ok(box);
  assert.equal(box.confirmedAt, bars[23].closedAt);
  bars.push(candle(24, 110));
  let result = analyze(bars);
  const broken = result.annotations.find(a => a.id === box.id);
  assert.equal(broken.state, 'BROKEN');
  assert.deepEqual(geometry(broken), geometry(box));
  bars.push(candle(25, 100));
  result = analyze(bars);
  assert.equal(result.annotations.find(a => a.id === box.id).state, 'BROKEN');
  assert.ok(result.annotations.some(a => a.rule === 'range-return-v1'));
  assert.equal(result.annotations.filter(a => a.kind === 'range').length, 1, 'no immediate post-break refit');
});

test('history gaps expire prior structures and prevent bridging FVG or ATR warmup', () => {
  const bars = oscillation(24);
  const box = analyze(bars).annotations.find(a => a.kind === 'range');
  bars.push(candle(30, 150));
  const result = analyze(bars);
  assert.equal(result.annotations.find(a => a.id === box.id).state, 'EXPIRED');
  assert.equal(result.atr, null);
  assert.equal(result.regime, 'UNAVAILABLE');
  assert.match(result.warnings.join(' '), /缺口/);
  assert.equal(result.annotations.filter(a => a.kind === 'fvg' && a.confirmedAt === bars.at(-1).closedAt).length, 0);
  assert.equal(result.volumeProfile.totalVolume, 10, 'profile uses only final contiguous segment');
  const broken = oscillation(24).concat(candle(24, 110), candle(26, 100));
  assert.equal(analyze(broken).annotations.some(a => a.rule === 'range-return-v1'), false, 'no range return inferred across missing bar');
});

test('previous UTC day/week references require complete contiguous coverage', () => {
  const bars = Array.from({length: 169}, (_, i) => candle(i, 100 + i / 100));
  assert.ok(analyze(bars.slice(0, 25)).annotations.some(a => a.rule === 'previous-day-high-v1'));
  assert.equal(analyze(bars.slice(1, 25)).annotations.some(a => a.rule === 'previous-day-high-v1'), false);
  assert.equal(analyze(bars.slice(0, 12).concat(bars.slice(13, 25))).annotations.some(a => a.rule === 'previous-day-high-v1'), false);
  const week = analyze(bars).annotations.find(a => a.rule === 'previous-week-high-v1');
  assert.ok(week);
  assert.equal(week.confirmedAt, bars[168].closedAt);
  assert.equal(week.high, Math.max(...bars.slice(0, 168).map(b => b.high)));
});

test('session boxes require full windows and apply IANA daylight-saving clocks', () => {
  function london(start) {
    return Array.from({length: 8}, (_, i) => candle(i, 100, {time: start + i * HOUR, closedAt: start + (i + 1) * HOUR}));
  }
  for (const start of [Date.UTC(2025, 0, 6, 8), Date.UTC(2025, 6, 7, 7)]) {
    const bars = london(start);
    const session = analyze(bars).annotations.find(a => a.rule === 'session-Europe/London-v1');
    assert.ok(session);
    assert.equal(session.start, start);
    assert.equal(session.confirmedAt, start + 8 * HOUR);
    assert.equal(analyze(bars.slice(1)).annotations.some(a => a.rule === 'session-Europe/London-v1'), false);
    assert.equal(analyze(bars.slice(0, 3).concat(bars.slice(4))).annotations.some(a => a.rule === 'session-Europe/London-v1'), false);
    const shifted = bars.map(b => ({...b, time: b.time + 30_000, closedAt: b.closedAt + 30_000}));
    assert.equal(analyze(shifted).annotations.some(a => a.rule === 'session-Europe/London-v1'), false, 'seconds-offset windows are incomplete');
  }
});

test('OHLCV profile conserves volume while gold tick/missing/zero volume stays unavailable', () => {
  const bars = oscillation(30);
  const profile = analyze(bars).volumeProfile;
  assert.equal(profile.kind, 'OHLCV_ESTIMATE');
  assert.equal(profile.totalVolume, 300);
  assert.ok(Math.abs(profile.bins.reduce((sum, b) => sum + b.volume, 0) - 300) < 1e-8);
  assert.ok(profile.val <= profile.poc && profile.poc <= profile.vah);
  for (const volumeKind of ['tick', 'unavailable']) {
    const result = analyze(bars, {instrumentId: 'XAUUSD', source: 'gold-provider', volumeKind});
    assert.equal(result.volumeProfile.kind, 'UNAVAILABLE');
    assert.deepEqual(result.volumeProfile.bins, []);
    assert.equal(result.volumeProfile.poc, null);
  }
  assert.equal(analyze(bars.map(b => ({...b, volume: 0}))).volumeProfile.kind, 'UNAVAILABLE');
  assert.equal(analyze(bars.map((b, i) => ({...b, volume: i === 3 ? null : 10}))).volumeProfile.kind, 'UNAVAILABLE');
});

test('instrument, provider and timeframe isolate annotation IDs', () => {
  const bars = oscillation(30);
  const first = new Set(analyze(bars).annotations.map(a => a.id));
  for (const changed of [{instrumentId: 'ETHUSDT'}, {source: 'other-feed'}, {timeframe: 'custom-hour'}]) {
    const result = analyze(bars, changed);
    assert.ok(result.annotations.length > 0);
    assert.ok(result.annotations.every(a => !first.has(a.id)));
  }
});

test('rising pivot channels keep fixed anchors and break on either side', () => {
  const bars = Array.from({length: 60}, (_, i) => candle(i, 100 + i * .6 + Math.sin(i * Math.PI / 3) * 3));
  const formed = analyze(bars).annotations.filter(a => a.kind === 'channel' && ['ACTIVE', 'TOUCHED'].includes(a.state)).at(-1);
  assert.ok(formed, 'a causal rising channel should be available');
  bars.push(candle(60, 190));
  const updated = analyze(bars).annotations.find(a => a.id === formed.id);
  assert.equal(updated.state, 'BROKEN');
  assert.deepEqual(geometry(updated), geometry(formed));
});
