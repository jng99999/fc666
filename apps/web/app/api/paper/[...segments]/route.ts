export const dynamic='force-dynamic';
async function proxy(request:Request,context:{params:Promise<{segments:string[]}>}){
 const {segments}=await context.params;
 const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 const valid=['sessions','streams'].includes(segments[0]??'')&&((segments.length===1&&['GET','POST'].includes(request.method))||(uuid.test(segments[1]??'')&&((segments.length===2&&request.method==='GET')||(segments.length===3&&((segments[2]==='command'&&request.method==='POST')||((segments[2]==='controls'||segments[0]==='streams'&&['recovery','intents','preparations'].includes(segments[2]))&&request.method==='GET'))))));
 if(!valid&&!(segments.length===1&&segments[0]==='status'&&request.method==='GET'))return Response.json({error:'Unsupported paper path'},{status:404});
 const body=request.method==='POST'?await request.text():undefined;
 if(body&&body.length>16384)return Response.json({error:'Request too large'},{status:413});
 try{const response=await fetch(new URL(`/api/v1/paper/${segments.join('/')}${new URL(request.url).search}`,process.env.API_BASE_URL??'http://127.0.0.1:8000'),{method:request.method,headers:{'Content-Type':'application/json'},body,cache:'no-store',signal:AbortSignal.timeout(10000)});return Response.json(await response.json(),{status:response.status,headers:{'Cache-Control':'no-store'}});}
 catch{return Response.json({error:'Paper service unavailable'},{status:502});}
}
export const GET=proxy,POST=proxy;
