import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../app/analysis/analysis-preferences.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',define:{'process.env.NEXT_PUBLIC_MARKET_PROVIDER':'"bitget"'}});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
const {readAnalysisPreferences,mergeAnalysisPreferences}=module.exports;

test('workspace preference merge preserves unrelated existing fields and validates the three selected fields',()=>{
 const original={symbol:'ETHUSDT',timeframe:'4h',watchlist:['ETHUSDT','SOLUSDT'],left:224,right:340,showLeft:false,showRight:true,tab:'订单',mobilePanel:'book'};
 const parsed=readAnalysisPreferences(JSON.stringify(original));
 assert.equal(parsed.symbol,'ETHUSDT');assert.equal(parsed.timeframe,'4h');assert.deepEqual(parsed.watchlist,['ETHUSDT','SOLUSDT']);
 const selection={symbol:'BTCUSDT',timeframe:'15m',watchlist:['ETHUSDT','SOLUSDT','BTCUSDT']};
 assert.deepEqual(JSON.parse(mergeAnalysisPreferences(JSON.stringify(original),selection)),{...original,...selection});
 for(const watchlist of [[],['ETHUSDT','ETHUSDT'],['XAUUSD'],['UNKNOWN']])assert.equal(readAnalysisPreferences(JSON.stringify({...original,watchlist})).watchlist,null);
 assert.equal(readAnalysisPreferences('{invalid').symbol,undefined);
 assert.equal(readAnalysisPreferences(JSON.stringify({symbol:'XAUUSD',timeframe:'2h'})).symbol,undefined);
 assert.equal(readAnalysisPreferences(JSON.stringify({timeframe:'2h'})).timeframe,undefined);
 assert.deepEqual(readAnalysisPreferences('[]').stored,{});
});
