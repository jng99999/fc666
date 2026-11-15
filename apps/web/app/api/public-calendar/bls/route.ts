import {createHash} from 'node:crypto';

export const dynamic='force-dynamic';
export const runtime='nodejs';
const CALENDAR='https://www.bls.gov/schedule/news_release/bls.ics';
const TTL=60_000,MAX_BYTES=1_500_000;
const headers={'Cache-Control':'no-store'};
type Resource={url:string;fetched_at:string;upstream_date:string|null;last_modified:string|null;age:string|null;sha256:string;content:string};
type Envelope={version:'bls-subscription-v1';scope_month:string;resources:{calendar:Resource;scope:Resource}};
let cached:{value:Envelope;at:number}|undefined;
let pending:Promise<Envelope>|undefined;
let nextAttempt=0;
let lastFailure='BLS_SUBSCRIPTION_UNAVAILABLE';

async function resource(url:string,calendar:boolean):Promise<Resource>{
 const response=await fetch(url,{cache:'no-store',redirect:'error',signal:AbortSignal.timeout(8000),headers:{'User-Agent':'FC666-official-calendar-subscription/1.0','Accept':'text/calendar,text/html;q=0.9','Cache-Control':'no-cache'}});
 if(!response.ok||!response.body)throw new Error(`BLS_HTTP_${response.status}`);
 const chunks:Uint8Array[]=[];let bytes=0;const reader=response.body.getReader();
 try{for(;;){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>MAX_BYTES)throw new Error('BLS response too large');chunks.push(value);}}
 finally{await reader.cancel();reader.releaseLock();}
 const content=Buffer.concat(chunks).toString('utf8');
 if(calendar?!(content.trim().startsWith('BEGIN:VCALENDAR')&&content.trim().endsWith('END:VCALENDAR')):!/<title>[^<]*\b20\d{2}\b[^<]*<\/title>/i.test(content))throw new Error('BLS_CONTENT_INVALID');
 const fetched=Date.now(),date=response.headers.get('date');
 if(!date||!Number.isFinite(Date.parse(date))||Date.parse(date)>fetched+120_000)throw new Error('Invalid BLS server clock');
 return {url,fetched_at:new Date(fetched).toISOString(),upstream_date:date,last_modified:response.headers.get('last-modified'),age:response.headers.get('age'),sha256:createHash('sha256').update(content).digest('hex'),content};
}

/** Fixed official subscription only: no client target, credentials or proxy headers. */
export async function GET(request:Request){
 if(new URL(request.url).search)return Response.json({detail:'Calendar subscription accepts no parameters'},{status:400,headers});
 const now=Date.now();
 const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit'}).formatToParts(new Date(now));
 const year=parts.find(part=>part.type==='year')!.value,month=parts.find(part=>part.type==='month')!.value;
 const scope_month=`${year}-${month}`;
 if(cached&&cached.value.scope_month===scope_month&&now>=cached.at&&now-cached.at<TTL)return Response.json(cached.value,{headers});
 if(!pending){
  if(now<nextAttempt)return Response.json({detail:'BLS subscription is unavailable; no fresh coverage granted',reason:lastFailure},{status:503,headers});
  nextAttempt=now+TTL;
  pending=(async()=>{
   const [calendar,scope]=await Promise.all([resource(CALENDAR,true),resource(`https://www.bls.gov/schedule/${year}/${month}_sched.htm`,false)]);
   const value:Envelope={version:'bls-subscription-v1',scope_month,resources:{calendar,scope}};
   cached={value,at:Math.min(Date.parse(calendar.fetched_at),Date.parse(scope.fetched_at))};return value;
  })();
 }
 try{return Response.json(await pending,{headers});}
 catch(error){cached=undefined;lastFailure=error instanceof Error&&/^BLS_(HTTP_\d{3}|CONTENT_INVALID)$/.test(error.message)?error.message:'BLS_TRANSPORT_OR_VALIDATION_FAILED';return Response.json({detail:'BLS subscription is unavailable; no fresh coverage granted',reason:lastFailure},{status:503,headers});}
 finally{pending=undefined;}
}
