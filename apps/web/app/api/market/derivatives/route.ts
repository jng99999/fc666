import {derivativeSymbol,fetchDerivativesSnapshot} from '../../../../lib/chart-derivatives';

export const dynamic='force-dynamic';
export async function GET(request:Request){
 const headers={'Cache-Control':'no-store'},url=new URL(request.url),instrument=url.searchParams.get('instrument')??'',symbol=derivativeSymbol(instrument);
 if(!symbol||[...url.searchParams.keys()].some(key=>key!=='instrument')||url.searchParams.getAll('instrument').length!==1)return Response.json({error:'仅支持指定 Bitget 现货产品对应的 USDT 合约公开背景。'},{status:400,headers});
 try{return Response.json(await fetchDerivativesSnapshot(symbol),{headers});}
 catch(error){const timeout=error instanceof Error&&(error.name==='TimeoutError'||error.name==='AbortError');return Response.json({error:timeout?'合约背景读取超时，请重试。':error instanceof Error?error.message:'合约背景暂不可用。'},{status:503,headers});}
}
