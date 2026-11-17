/** Labels may move for legibility; their anchor always remains the actual chart coordinate. */
export type ChartOverlayLabel = {id:string;text:string;title:string;x:number;y:number;color:string;priority:number;selectionId?:string};
export type PlacedChartLabel = ChartOverlayLabel & {left:number;top:number;width:number;height:number;displayText:string};
const inset=4,gap=4,height=22;
const textWidth=(text:string)=>Array.from(text).reduce((sum,c)=>sum+(/[^\x00-\x7f]/.test(c)?10:6),0);
function fitText(text:string,available:number){
 if(textWidth(text)<=available)return text;
 let fitted='';
 for(const char of text){if(textWidth(fitted+char+'…')>available)break;fitted+=char;}
 return fitted+'…';
}

/** Selected labels go first. Crowded secondary labels are omitted, never stacked or put outside the plot. */
export function layoutChartLabels(labels:readonly ChartOverlayLabel[],viewport:{width:number;height:number}):PlacedChartLabel[]{
 if(!Number.isFinite(viewport.width)||!Number.isFinite(viewport.height)||viewport.width<44||viewport.height<height+inset*2)return [];
 const placed:PlacedChartLabel[]=[],maxWidth=Math.min(viewport.width<500?132:176,viewport.width-inset*2),limit=viewport.width<500?8:12;
 for(const item of [...labels].sort((a,b)=>b.priority-a.priority)){
  if(placed.length>=limit)break;
  if(![item.x,item.y,item.priority].every(Number.isFinite)||item.y<0||item.y>viewport.height||!item.text)continue;
  const width=Math.min(maxWidth,Math.max(32,textWidth(item.text)+16));
  const left=Math.max(inset,Math.min(viewport.width-inset-width,item.x+6));
  const naturalTop=Math.max(inset,Math.min(viewport.height-inset-height,item.y-height-5));
  // Keep labels near their anchors; the unplaced regions remain available through the region list.
  const offsets=[0,-26,26,-52,52,-78,78];
  let top:number|undefined;
  for(const offset of offsets){
   const candidate=naturalTop+offset;
   if(candidate<inset||candidate+height>viewport.height-inset)continue;
   if(placed.some(other=>left<other.left+other.width+gap&&left+width+gap>other.left&&candidate<other.top+other.height+gap&&candidate+height+gap>other.top))continue;
   top=candidate;break;
  }
  if(top===undefined)continue;
  placed.push({...item,left,top,width,height,displayText:fitText(item.text,width-16)});
 }
 return placed;
}
