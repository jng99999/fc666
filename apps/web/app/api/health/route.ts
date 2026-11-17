export const dynamic = "force-dynamic";
export async function GET() {
  const started=Date.now(),requestId=crypto.randomUUID();
  let upstreamStatus:number|undefined;
  try {
    const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/health/ready`, {cache:"no-store", signal:AbortSignal.timeout(5000)});
    upstreamStatus=response.status;
    if (response.status !== 200 && response.status !== 503) throw new Error("upstream unavailable");
    const body=await response.json();
    if(response.status===503)console.warn('health_upstream_unavailable',{request_id:requestId,status:upstreamStatus,elapsed_ms:Date.now()-started});
    return Response.json(body, {status:response.status, headers:{"Cache-Control":"no-store","X-FC666-Health-Source":"upstream","X-FC666-Request-Id":requestId}});
  } catch(error) {
    console.warn('health_proxy_failure',{request_id:requestId,status:upstreamStatus,kind:error instanceof Error?error.name:'Unknown',elapsed_ms:Date.now()-started});
    return Response.json({error:"API unavailable"}, {status:502,headers:{"Cache-Control":"no-store","X-FC666-Health-Source":"proxy","X-FC666-Request-Id":requestId}});
  }
}
