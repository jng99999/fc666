export const dynamic = "force-dynamic";
const paths = new Map([
  ["status", "/api/v1/market/status"],
  ["snapshot", "/api/v1/market/snapshot"],
  ["instruments", "/api/v1/instruments"],
  ["candles", "/api/v1/candles"],
  ["indicators", "/api/v1/indicators"],
]);
export async function GET(request: Request, context: {params: Promise<{resource: string}>}) {
  const {resource} = await context.params;
  const path = paths.get(resource);
  if (!path) return Response.json({error:"Unknown public resource"}, {status:404});
  try {
    const target = new URL(path, process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
    target.search = new URL(request.url).search;
    const response = await fetch(target, {cache:"no-store", signal:AbortSignal.timeout(5000)});
    return Response.json(await response.json(), {status:response.status, headers:{"Cache-Control":"no-store"}});
  } catch {return Response.json({error:"Public data service unavailable"}, {status:502});}
}

export async function POST(request:Request,context:{params:Promise<{resource:string}>}) {
  const {resource}=await context.params;
  if(resource!=="backtest")return Response.json({error:"Unknown research resource"},{status:404});
  const body=await request.text();
  if(body.length>16384)return Response.json({error:"Request too large"},{status:413});
  try{
    const response=await fetch(new URL("/api/v1/research/backtest",process.env.API_BASE_URL??"http://127.0.0.1:8000"),{method:"POST",headers:{"Content-Type":"application/json"},body,cache:"no-store",signal:AbortSignal.timeout(10000)});
    return Response.json(await response.json(),{status:response.status,headers:{"Cache-Control":"no-store"}});
  }catch{return Response.json({error:"Research service unavailable"},{status:502});}
}
