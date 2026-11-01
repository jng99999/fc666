import {OperatorStatus,OperatorConfiguration,OperatorAccounts} from '../../../../lib/operator';

export const dynamic='force-dynamic';
const limit=16384;
const cookiePattern=/^(fc666_admin_session|fc666_directional_session)=[A-Za-z0-9_-]{43}$/;
const headers={'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'};

async function boundedBody(request:Request){
 const size=request.headers.get('content-length');if(size&&(!/^\d+$/.test(size)||Number(size)>limit))throw new Error('oversize');
 if(!request.body)return '';
 const reader=request.body.getReader();let total=0;const chunks:Uint8Array[]=[];
 try{for(;;){const next=await reader.read();if(next.done)break;total+=next.value.byteLength;if(total>limit){await reader.cancel();throw new Error('oversize');}chunks.push(next.value);}}
 finally{reader.releaseLock();}
 const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
 return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
}
function message(status:number){return status===401?'管理员会话失效或登录信息错误，请重新登录。':status===403?'登录权限或请求校验未通过，请刷新后重试。':status===409?'配置版本已变化，请重新读取并确认；旧配置仍保留。':status===429?'尝试过于频繁，请稍后再试。':status===400||status===422?'填写内容未通过服务器校验，请检查字段、接口地址与账户归属。':'管理员服务暂不可用，未确认保存结果，请先刷新核对。';}

async function proxy(request:Request,context:{params:Promise<{segments:string[]}>}){
 const {segments}=await context.params,resource=segments[0],read=request.method==='GET';
 const valid=segments.length===1&&(read?['status','configuration','accounts'].includes(resource):request.method==='POST'&&['setup','login','logout','configuration','password'].includes(resource));
 if(!valid)return Response.json({detail:'Unsupported administrator resource'},{status:404,headers});
 const base=process.env.DIRECTIONAL_API_BASE_URL;
 if(!base)return Response.json({detail:'管理员服务尚未连接，请联系站点运营者。'},{status:503,headers});
 try{
  const endpoint=new URL(base),loopback=['localhost','127.0.0.1','[::1]'].includes(endpoint.hostname);
  if(endpoint.username||endpoint.password||endpoint.search||endpoint.hash||endpoint.protocol!=='https:'&&!(endpoint.protocol==='http:'&&loopback))throw new Error('unsafe administrator transport');
 }catch{return Response.json({detail:'管理员服务连接必须使用 HTTPS，未发送登录信息或密钥。'},{status:503,headers});}
 let body:string|undefined;
 if(!read){
  const internal=new URL(request.url),protocol=request.headers.get('x-forwarded-proto')?.split(',')[0].trim();
  const expected=(protocol==='http'||protocol==='https'?protocol:internal.protocol.slice(0,-1))+'://'+(request.headers.get('host')??internal.host);
  if(request.headers.get('origin')!==expected||request.headers.get('sec-fetch-site')==='cross-site')return Response.json({detail:'请从当前站点提交管理员操作。'},{status:403,headers});
  if(request.headers.get('content-type')?.split(';')[0].trim()!=='application/json')return Response.json({detail:'JSON requests required'},{status:415,headers});
  if(!['login','setup'].includes(resource)&&!request.headers.get('x-csrf-token'))return Response.json({detail:'会话校验已失效，请刷新登录状态。'},{status:403,headers});
  try{body=await boundedBody(request);}catch{return Response.json({detail:'请求内容过大或编码无效。'},{status:413,headers});}
 }
 const cookies=request.headers.get('cookie')?.split(';').map(value=>value.trim()).filter(value=>cookiePattern.test(value)).join('; ');
 if(!['status','login','setup'].includes(resource)&&!cookies?.split('; ').some(value=>value.startsWith('fc666_admin_session=')))return Response.json({detail:message(401)},{status:401,headers});
 try{
  const response=await fetch(new URL('/api/v1/directional/admin/'+resource,base),{
   method:request.method,headers:{'Content-Type':'application/json',...(cookies?{Cookie:cookies}:{}),...(!read?{Origin:request.headers.get('origin')!,'X-CSRF-Token':request.headers.get('x-csrf-token')??''}:{})},
   body,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(12000),
  });
  const forwarded=new Headers(headers);
  const session=response.headers.get('set-cookie');
  if(session&&/^fc666_admin_session=/.test(session)&&!/[\r\n]/.test(session)){
   const parts=session.split(';').map(value=>value.trim());
   if(/^fc666_admin_session=(?:[A-Za-z0-9_-]{43}|"")?$/.test(parts[0])){
    const pair=parts[0]==='fc666_admin_session=""'?'fc666_admin_session=':parts[0];
    const maxAge=parts.find(value=>/^max-age=/i.test(value));
    const seconds=pair==='fc666_admin_session='?0:maxAge&&/^max-age=\d+$/i.test(maxAge)?Math.min(Number(maxAge.split('=')[1]),28800):28800;
    forwarded.set('Set-Cookie',`${pair}; Path=/; Max-Age=${seconds}; Secure; HttpOnly; SameSite=Strict`);
   }
  }
  if(!response.ok)return Response.json({detail:message(response.status)},{status:[400,401,403,409,413,415,422,429,503].includes(response.status)?response.status:502,headers:forwarded});
  const value:unknown=await response.json();
  const schema=resource==='configuration'?OperatorConfiguration:resource==='accounts'?OperatorAccounts:OperatorStatus;
  const parsed=schema.safeParse(value);
  if(!parsed.success)return Response.json({detail:'管理员响应校验失败，未显示未确认的配置。'},{status:502,headers:forwarded});
  return Response.json(parsed.data,{status:response.status,headers:forwarded});
 }catch{return Response.json({detail:message(502)},{status:502,headers});}
}
export const GET=proxy,POST=proxy;
