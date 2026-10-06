export const dynamic='force-dynamic';
export async function POST(request:Request){
 const body=await request.text();if(body.length>16384)return Response.json({error:'Request too large'},{status:413});
 try{const response=await fetch(new URL('/api/v1/portfolio/valuation',process.env.API_BASE_URL??'http://127.0.0.1:8000'),{method:'POST',headers:{'Content-Type':'application/json'},body,cache:'no-store',signal:AbortSignal.timeout(15000)});return Response.json(await response.json(),{status:response.status,headers:{'Cache-Control':'no-store'}});}catch{return Response.json({error:'Valuation service unavailable'},{status:502});}
}
