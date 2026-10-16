import type {Candle} from './market';

// Descriptive floats only. Input is the validated, contiguous closed window.
export function technicalSeries(bars:Candle[]){
 const closes=bars.map(b=>Number(b.close)),typical=bars.map(b=>(Number(b.high)+Number(b.low)+Number(b.close))/3);
 const mean=(values:number[])=>values[0]+values.reduce((sum,value)=>sum+(value-values[0]),0)/values.length;
 const sma=(i:number,p:number)=>i+1<p?null:mean(closes.slice(i-p+1,i+1));
 const fastK:(number|null)[]=[];let obv=0;
 return bars.map((bar,i)=>{
  const close=closes[i],range=bars.slice(Math.max(0,i-13),i+1),high=Math.max(...range.map(b=>Number(b.high))),low=Math.min(...range.map(b=>Number(b.low)));
  const k=i<13||high===low?null:100*(close-low)/(high-low);fastK.push(k);
  const dWindow=fastK.slice(i-2,i+1),stochD=dWindow.length===3&&dWindow.every((v):v is number=>v!==null)?mean(dWindow):null;
  const tp=typical.slice(Math.max(0,i-19),i+1),average=mean(tp),deviation=mean(tp.map(v=>Math.abs(v-average)));
  if(i>0)obv+=Math.sign(close-closes[i-1])*Number(bar.volume);
  return {open_time:bar.open_time,ma5:sma(i,5),ma10:sma(i,10),ma60:sma(i,60),stochK:k,stochD,wr:k===null?null:k-100,
   cci:i<19||deviation===0?null:(typical[i]-average)/(.015*deviation),roc:i<12?null:(close/closes[i-12]-1)*100,obv};
 });
}
export type TechnicalRow=ReturnType<typeof technicalSeries>[number];
