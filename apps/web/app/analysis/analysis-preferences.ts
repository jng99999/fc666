import {Intervals,Symbols,type Interval,type SymbolName} from '../../lib/market';
export const workspacePreferenceKey='fc666.workspace.v1';
export function readAnalysisPreferences(raw:string|null){
 let stored:Record<string,unknown>={};
 try{const value=raw?JSON.parse(raw):null;if(value&&typeof value==='object'&&!Array.isArray(value))stored=value;}catch{}
 const symbol=Symbols.find(value=>value===stored.symbol),timeframe=Intervals.find(value=>value===stored.timeframe);
 const watchlist=Array.isArray(stored.watchlist)&&stored.watchlist.length>0&&stored.watchlist.length<=Symbols.length&&new Set(stored.watchlist).size===stored.watchlist.length&&stored.watchlist.every(value=>Symbols.some(symbol=>symbol===value))?stored.watchlist as SymbolName[]:null;
 return {stored,symbol,timeframe,watchlist};
}
export function mergeAnalysisPreferences(raw:string|null,selection:{symbol:SymbolName;timeframe:Interval;watchlist:SymbolName[]}){
 return JSON.stringify({...readAnalysisPreferences(raw).stored,...selection});
}
