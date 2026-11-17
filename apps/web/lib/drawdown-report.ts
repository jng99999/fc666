export type DrawdownRow={as_of:string;drawdown:string};
export type DrawdownEpisode={start:string;trough:string;end:string;recovered:boolean;depth:number;seconds:number};
export type DrawdownReport={version:'drawdown-report-v1';samples:number;ulcerIndex:number;underwaterFraction:number;returnToDrawdown:number|null;longestSeconds:number;episodes:DrawdownEpisode[]};

// Derived display report only: never mutate the frozen engine result or its hash.
export function drawdownReport(rows:readonly DrawdownRow[],totalReturn:string):DrawdownReport|null{
 if(rows.length<2||totalReturn.trim()===''||!Number.isFinite(Number(totalReturn)))return null;
 let previous=-Infinity,sumSquares=0,underwater=0,maximum=0;
 let active:DrawdownEpisode|null=null;
 const episodes:DrawdownEpisode[]=[];
 for(const row of rows){
  const time=Date.parse(row.as_of),depth=Number(row.drawdown);
  if(!/(Z|[+-]\d{2}:\d{2})$/.test(row.as_of)||!Number.isFinite(time)||time<=previous||row.drawdown.trim()===''||!Number.isFinite(depth)||depth<0||depth>1)return null;
  previous=time;sumSquares+=depth*depth;maximum=Math.max(maximum,depth);
  if(depth>0){
   underwater++;
   if(!active)active={start:row.as_of,trough:row.as_of,end:row.as_of,recovered:false,depth,seconds:0};
   if(depth>active.depth){active.depth=depth;active.trough=row.as_of;}
   active.end=row.as_of;active.seconds=(time-Date.parse(active.start))/1000;
  }else if(active){
   active.end=row.as_of;active.seconds=(time-Date.parse(active.start))/1000;active.recovered=true;
   episodes.push(active);active=null;
  }
 }
 if(active)episodes.push(active);
 const returnToDrawdown=maximum===0?null:Number(totalReturn)/maximum;
 return {version:'drawdown-report-v1',samples:rows.length,ulcerIndex:Math.sqrt(sumSquares/rows.length),underwaterFraction:underwater/rows.length,
  returnToDrawdown:returnToDrawdown!==null&&Number.isFinite(returnToDrawdown)?returnToDrawdown:null,
  longestSeconds:Math.max(0,...episodes.map(episode=>episode.seconds)),episodes};
}
