export const dynamic = "force-dynamic";
export async function GET() {
  try {
    const response = await fetch(`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/health/ready`, {cache:"no-store", signal:AbortSignal.timeout(5000)});
    if (response.status !== 200 && response.status !== 503) throw new Error("upstream unavailable");
    return Response.json(await response.json(), {status:response.status, headers:{"Cache-Control":"no-store"}});
  } catch {
    return Response.json({error:"API unavailable"}, {status:502});
  }
}
