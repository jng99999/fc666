'use client';
import {useEffect,useRef,type ReactNode} from 'react';

export default function AnalysisDialog({title,onClose,children,wide=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean}){
 const dialog=useRef<HTMLDialogElement>(null),close=useRef(onClose);
 close.current=onClose;
 useEffect(()=>{
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const current=dialog.current;
  current?.showModal();
  return()=>{current?.close();previous?.focus();};
 },[]);
 return <dialog ref={dialog} className={`analysis-modal${wide?' analysis-modal-wide':''}`} aria-labelledby="analysis-modal-title" onCancel={event=>{event.preventDefault();close.current();}} onClick={event=>{if(event.target===dialog.current)close.current();}}>
  <header className="dialog-header"><h2 id="analysis-modal-title">{title}</h2><button type="button" className="icon-btn" aria-label="关闭弹窗" onClick={onClose}><svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
  <div className="analysis-modal-body">{children}</div>
 </dialog>;
}
