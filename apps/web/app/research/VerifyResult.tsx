"use client";
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
const Receipt=z.object({run_id:z.string(),engine_version:z.string(),result_sha256:z.string().regex(/^[0-9a-f]{64}$/),snapshot_sha256:z.string().regex(/^[0-9a-f]{64}$/),consistency_verified:z.literal(true),provenance_authenticated:z.literal(false),trading_enabled:z.literal(false)});
export default function VerifyResult({jobId,runId}:{jobId:string;runId:string}){
 const [state,setState]=useState<'idle'|'busy'|'success'|'error'>('idle');
 const controller=useRef<AbortController|null>(null);
 useEffect(()=>()=>controller.current?.abort(),[]);
 async function verify(){
  if(controller.current)return;
  const abort=new AbortController();controller.current=abort;setState('busy');
  try{
   const response=await fetch(`/api/research/jobs/${jobId}/verify`,{method:'POST',cache:'no-store',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});
   if(!response.ok)throw new Error('Verification failed');
   const receipt=Receipt.parse(await response.json());
   if(receipt.run_id!==runId)throw new Error('Displayed report mismatch');
   if(!abort.signal.aborted)setState('success');
  }catch{if(!abort.signal.aborted)setState('error');}
  finally{if(controller.current===abort)controller.current=null;}
 }
 return <div data-testid="research-verification"><button disabled={state==='busy'} onClick={()=>void verify()}>{state==='busy'?'正在重算复验…':'复验冻结结果'}</button><p role={state==='error'?'alert':'status'}>{state==='success'?'复验通过：保存的报告与冻结数据重算结果一致。':state==='error'?'复验未通过或服务不可用，请稍后重试。':state==='busy'?'正在用冻结数据重新计算，请稍候。':'尚未复验。'}</p><small>复验不证明数据来源可信或策略盈利。</small></div>;
}
