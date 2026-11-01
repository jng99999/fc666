export const dynamic='force-dynamic';

async function proxy(request:Request,context:{params:Promise<{segments:string[]}>}){
 const {segments}=await context.params;
 const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 const read=request.method==='GET';
 const valid=segments.length===1&&((segments[0]==='status'&&read)||(segments[0]==='accounts'&&['GET','POST'].includes(request.method)))||
  segments[0]==='accounts'&&uuid.test(segments[1]??'')&&((segments.length===2&&read)||(segments.length===3&&['decision','stop'].includes(segments[2])&&request.method==='POST'))||
  segments[0]==='research'&&((segments.length===2&&['status','jobs'].includes(segments[1])&&read)||
   (segments.length===3&&segments[1]==='jobs'&&uuid.test(segments[2])&&read)||
   (segments.length===3&&segments[1]==='accounts'&&uuid.test(segments[2])&&request.method==='POST')||
   (segments.length===3&&segments[1]==='automation'&&uuid.test(segments[2])&&['GET','POST'].includes(request.method)));
 const headers={'Cache-Control':'no-store'};
 if(!valid)return Response.json({detail:'Unsupported directional resource'},{status:404,headers});
 const configured=process.env.DIRECTIONAL_API_BASE_URL;
 if(!configured)return Response.json({detail:'独立双向模拟服务尚未连接，未发送账户操作。'},{status:503,headers});
 if(!read){
  const origin=request.headers.get('origin');
  const internal=new URL(request.url),protocol=request.headers.get('x-forwarded-proto')?.split(',')[0].trim();
  const expected=(protocol==='http'||protocol==='https'?protocol:internal.protocol.slice(0,-1))+'://'+(request.headers.get('host')??internal.host);
  if(request.headers.get('sec-fetch-site')==='cross-site'||origin!==expected)return Response.json({detail:'Same-origin commands required'},{status:403,headers});
  if(request.headers.get('content-type')?.split(';')[0]!=='application/json')return Response.json({detail:'JSON requests required'},{status:415,headers});
 }
 const body=read?undefined:await request.text();
 if(body&&new TextEncoder().encode(body).length>8192)return Response.json({detail:'Request too large'},{status:413,headers});
 const cookie=request.headers.get('cookie')?.split(';').map(value=>value.trim()).filter(value=>/^(?:fc666_directional_session|fc666_admin_session)=[A-Za-z0-9_-]{43}$/.test(value)).join('; ');
 try{
  const response=await fetch(new URL('/api/v1/directional/'+segments.join('/'),configured),{method:request.method,headers:{'Content-Type':'application/json',...(cookie?{Cookie:cookie}:{}),...(!read?{Origin:request.headers.get('origin')??''}:{})},body,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(12000)});
  const forwarded=new Headers(headers),session=response.headers.get('set-cookie');
  if(session)forwarded.set('Set-Cookie',session);
  return Response.json(await response.json(),{status:response.status,headers:forwarded});
 }catch{
  return Response.json({detail:read?'独立模拟服务暂不可用，保留最后确认账本。':segments[0]==='research'?'研究请求结果尚未确认，请重试同一请求或刷新研究记录；不会自动下单。':'提交结果尚未确认，请先刷新账户核对；不要另建请求重复下单。'},{status:502,headers});
 }
}
export const GET=proxy,POST=proxy;
