'use client';
import {useEffect,useId,useRef,type ReactNode} from 'react';

export default function AnalysisDialog({title,onClose,children,wide=false,immersive=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean;immersive?:boolean}){
 const titleId=useId(),dialog=useRef<HTMLDialogElement>(null),close=useRef(onClose);
 close.current=onClose;
 useEffect(()=>{
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const current=dialog.current;
  const overflow=document.body.style.overflow;
  if(immersive)document.body.style.overflow='hidden';
  current?.showModal();
  return()=>{current?.close();if(immersive)document.body.style.overflow=overflow;previous?.focus();};
 },[immersive]);
 return <dialog ref={dialog} className={`analysis-modal${wide?' analysis-modal-wide':''}${immersive?' analysis-modal-immersive':''}`} aria-labelledby={titleId} onCancel={event=>{event.preventDefault();close.current();}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close.current();}}} onClick={event=>{if(event.target===event.currentTarget){const box=event.currentTarget.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)close.current();}}}>
  <header className="dialog-header"><h2 id={titleId}>{title}</h2><button type="button" className="icon-btn" aria-label="关闭弹窗" onClick={onClose}><svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
  <div className="analysis-modal-body">{children}</div>
 </dialog>;
}
