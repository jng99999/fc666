export const dynamic='force-dynamic';
async function proxy(request:Request,{params}:{params:Promise<{segments:string[]}>}){
 const {segments}=await params;const uuid='[0-9a-fA-F-]{36}';const path=segments.join('/');
 if(!new RegExp(`^scenarios(?:/${uuid}(?:/snapshots(?:/${uuid})?|/analysis|/sampled|/risk)?)?$`).test(path))return Response.json({error:'Unknown route'},{status:404});
 const body=request.method==='POST'?await request.text():undefined;
 if(body&&body.length>16384)return Response.json({error:'Request too large'},{status:413});
 try{const target=new URL(`/api/v1/portfolio/${path}`,process.env.API_BASE_URL??'http://127.0.0.1:8000');target.search=new URL(request.url).search;
 const response=await fetch(target,{method:request.method,headers:{'Content-Type':'application/json'},body,cache:'no-store',signal:AbortSignal.timeout(20000)});
 return Response.json(await response.json(),{status:response.status,headers:{'Cache-Control':'no-store'}});
 }catch{return Response.json({error:'History service unavailable; submission outcome unknown'},{status:502});}
}
export const GET=proxy;export const POST=proxy;
