export const dynamic='force-dynamic';
async function proxy(request:Request,context:{params:Promise<{segments:string[]}>}){
 const {segments}=await context.params;
 const id=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 const research=segments[0]==='research'&&((segments.length===2&&((segments[1]==='catalogue'&&request.method==='GET')||(segments[1]==='runs'&&['GET','POST'].includes(request.method))))||(segments[1]==='runs'&&id.test(segments[2]??'')&&request.method==='GET'&&(segments.length===3||(segments.length===4&&segments[3]==='manifest')||(segments.length===5&&segments[3]==='members'&&/^\d{1,3}$/.test(segments[4])))));
 const policy=segments[0]==='policy'&&((segments.length===2&&((segments[1]==='status'&&request.method==='GET')||(segments[1]==='accounts'&&['GET','POST'].includes(request.method))))||(segments[1]==='requests'&&segments.length===3&&id.test(segments[2]??'')&&request.method==='GET')||(segments[1]==='accounts'&&id.test(segments[2]??'')&&((segments.length===3&&request.method==='GET')||(segments.length===4&&segments[3]==='command'&&request.method==='POST'))));
 const valid=research||policy||(segments.length===1&&((segments[0]==='market'&&request.method==='GET')||(segments[0]==='accounts'&&['GET','POST'].includes(request.method))))||
  (segments[0]==='accounts'&&id.test(segments[1]??'')&&((segments.length===2&&request.method==='GET')||(segments.length===3&&((segments[2]==='command'&&request.method==='POST')||(segments[2]==='events'&&request.method==='GET')))));
 if(!valid)return Response.json({detail:'Unsupported gold resource'},{status:404});
 const origin=request.headers.get('origin');let sameOrigin=true;
 if(origin){try{const expectedHost=request.headers.get('host')??new URL(request.url).host;const expectedProtocol=request.headers.get('x-forwarded-proto')?.split(',')[0].trim()??new URL(request.url).protocol.slice(0,-1);sameOrigin=new URL(origin).origin===`${expectedProtocol}://${expectedHost}`;}catch{sameOrigin=false;}}
 if(request.method==='POST'&&(!sameOrigin||request.headers.get('sec-fetch-site')==='cross-site'))return Response.json({detail:'Same-origin gold commands required'},{status:403});
 const body=request.method==='POST'?await request.text():undefined;
 if(body&&new TextEncoder().encode(body).length>8192)return Response.json({detail:'Gold request too large'},{status:413});
 const token=request.headers.get('cookie')?.split(';').map(v=>v.trim()).find(v=>/^fc666_gold_session=[A-Za-z0-9_-]{43}$/.test(v));
 const read=request.method==='GET',deadline=AbortSignal.timeout(12000),started=Date.now();
 for(let attempt=0;attempt<(read?2:1);attempt++){
  let status=0,contentType='';
  try{
   const response=await fetch(new URL(`/api/v1/gold/${segments.join('/')}${new URL(request.url).search}`,process.env.GOLD_API_BASE_URL??process.env.API_BASE_URL??'http://127.0.0.1:8017'),{
    method:request.method,headers:{'Content-Type':'application/json',...(token?{Cookie:token}:{})},body,cache:'no-store',signal:deadline});
   status=response.status;contentType=response.headers.get('content-type')?.split(';')[0]??'';
   if(read&&attempt===0&&[502,503,504].includes(status)&&!deadline.aborted){
    console.warn('gold_proxy_read_retry',{resource:segments[0],status,elapsed_ms:Date.now()-started});
    await response.body?.cancel();continue;
   }
   const headers=new Headers({'Cache-Control':'no-store'});const cookie=response.headers.get('set-cookie');if(cookie)headers.set('Set-Cookie',cookie);
   return Response.json(await response.json(),{status:response.status,headers});
  }catch(error){
   const cause=error instanceof Error?error.cause:undefined;
   const code=typeof cause==='object'&&cause!==null&&'code' in cause&&typeof cause.code==='string'&&/^[A-Z0-9_]{1,40}$/.test(cause.code)?cause.code:undefined;
   const retry=read&&attempt===0&&error instanceof TypeError&&!deadline.aborted;
   console.warn('gold_proxy_failure',{method:request.method,resource:segments[0],status,content_type:contentType,kind:error instanceof Error?error.name:'Unknown',code,retry,elapsed_ms:Date.now()-started});
   if(retry)continue;
   break;
  }
 }
 return Response.json({detail:read?'黄金服务读取暂不可用，请稍后刷新；已保存的研究和账本不会重置。':'黄金服务暂不可用，请刷新核对账本；不要重复提交未知结果的订单。'},{status:502,headers:{'Cache-Control':'no-store'}});
}
export const GET=proxy,POST=proxy;
