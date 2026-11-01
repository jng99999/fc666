'use client';
import {useEffect,useRef,useState} from 'react';
import {stopConfirmationReady,type AccountIdentity} from '../../../lib/paper-account-selection';

export type StopTarget=AccountIdentity&{symbol:string;timeframe:string};
export default function StopAccountDialog({target,busy,error,onCancel,onConfirm}:{target:StopTarget;busy:boolean;error:string;onCancel:()=>void;onConfirm:()=>Promise<void>}){
 const dialog=useRef<HTMLDialogElement>(null),cancel=useRef<HTMLButtonElement>(null),[confirmation,setConfirmation]=useState('');
 useEffect(()=>{const element=dialog.current;if(!element)return;const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;cancel.current?.setAttribute('autofocus','');element.showModal();cancel.current?.focus({preventScroll:true});return()=>{element.close();previous?.focus({preventScroll:true});};},[]);
 return <dialog ref={dialog} className="paper-stop-dialog" aria-labelledby="paper-stop-title" aria-describedby="paper-stop-description" onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
  <div className="paper-stop-heading"><span className="paper-danger-label">永久操作</span><h2 id="paper-stop-title">停止这个模拟账户？</h2><p id="paper-stop-description">停止后不能恢复运行。保留全部账本、现金和库存，不会自动平仓。</p></div>
  <dl className="paper-stop-identity"><div><dt>模拟市场</dt><dd>{target.symbol} · {target.timeframe}</dd></div><div><dt>账户编号</dt><dd><code>{target.id}</code></dd></div></dl>
  <form onSubmit={event=>{event.preventDefault();if(!busy&&stopConfirmationReady(target,confirmation))void onConfirm();}}>
   <label htmlFor="paper-stop-code">输入账户末 4 位 <strong>{target.id.slice(-4)}</strong> 确认身份</label>
   <input id="paper-stop-code" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={4} value={confirmation} disabled={busy} onChange={event=>setConfirmation(event.target.value)} aria-describedby="paper-stop-hint"/>
   <p id="paper-stop-hint">临时停用请取消，改用“暂停实时成交”。</p>
   {error?<p role="alert">{error}</p>:null}
   <div className="paper-stop-actions"><button ref={cancel} type="button" className="secondary" disabled={busy} onClick={onCancel}>取消</button><button type="submit" className="danger" disabled={busy||!stopConfirmationReady(target,confirmation)}>{busy?'正在核对并停止…':`确认停止 ${target.symbol}`}</button></div>
  </form>
 </dialog>;
}
