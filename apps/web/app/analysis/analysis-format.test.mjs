import test from 'node:test';
import assert from 'node:assert/strict';
import {decimalPlaces,displayPrecision,formatPrice} from './analysis-format.ts';

test('source price typography uses actual tick scale and keeps missing metadata explicit',()=>{
 assert.equal(formatPrice(81978.9,'BTCUSDT',undefined,'81978.9'),'81,978.90');
 assert.equal(formatPrice(2400,'ETHUSDT','0.010000000000000000','2400'),'2,400.00');
 assert.equal(decimalPlaces('0.000010000000000000'),5);
 assert.equal(decimalPlaces('1E-8'),8);
 assert.equal(decimalPlaces('0.2500'),2);
 assert.equal(displayPrecision('DOGEUSDT','0.000010000000000000','0.08'),5);
 assert.equal(formatPrice(.08,'DOGEUSDT','0.00001','0.08'),'0.08000');
 assert.equal(displayPrecision('DOGEUSDT',undefined,'0.08123'),5);
 assert.equal(displayPrecision('DOGEUSDT','invalid',undefined),null);
 assert.equal(formatPrice(NaN,'BTCUSDT',undefined,undefined),'—');
 assert.equal(formatPrice(null,'BTCUSDT',undefined,undefined),'—');
});
