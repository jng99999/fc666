export const dynamic='force-dynamic';
function pathFor(segments:string[],method:string){
 if(segments.length===1&&['jobs','strategies','status'].includes(segments[0])&&method==='GET')return segments.join('/');
 if(segments.length===1&&['jobs','batches','holdouts','walk-forwards'].includes(segments[0])&&method==='POST')return segments[0];
 if(!['jobs','batches'].includes(segments[0])||! /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(segments[1]??''))return null;
 if(segments.length===2&&method==='GET')return segments.join('/');
 if(segments.length===3&&((segments[0]==='jobs'&&segments[2]==='result'&&method==='GET')||(segments[2]==='cancel'&&method==='POST')))return segments.join('/');
 return null;
}
async function proxy(request:Request,context:{params:Promise<{segments:string[]}>}){
 const {segments}=await context.params;const path=pathFor(segments,request.method);
 if(!path)return Response.json({error:'Unsupported research path'},{status:404});
 const body=request.method==='POST'?await request.text():undefined;
 if(body&&body.length>16384)return Response.json({error:'Request too large'},{status:413});
 try{
  const response=await fetch(new URL(`/api/v1/research/${path}`,process.env.API_BASE_URL??'http://127.0.0.1:8000'),{method:request.method,headers:{'Content-Type':'application/json'},body,cache:'no-store',signal:AbortSignal.timeout(10000)});
  return Response.json(await response.json(),{status:response.status,headers:{'Cache-Control':'no-store'}});
 }catch{return Response.json({error:'Research service unavailable'},{status:502});}
}
export const GET=proxy,POST=proxy;
