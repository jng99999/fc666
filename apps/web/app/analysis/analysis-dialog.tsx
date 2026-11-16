'use client';
import {useEffect,useId,useRef,useState,type ReactNode} from 'react';
import {mobileExchangeQuery} from '../../lib/chart-landscape';
import './chart-landscape.css';

export default function AnalysisDialog({title,onClose,children,wide=false,immersive=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean;immersive?:boolean}){
 const titleId=useId(),dialog=useRef<HTMLDialogElement>(null),close=useRef(onClose);
 const [mobile,setMobile]=useState(false);
 useEffect(()=>{const media=window.matchMedia(mobileExchangeQuery),update=()=>setMobile(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 close.current=onClose;
 useEffect(()=>{
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const current=dialog.current;
  const overflow=document.body.style.overflow;
  const fitOverlay=()=>{const frame=document.querySelector<HTMLElement>('.analysis-modal-immersive [data-chart-frame]');current?.toggleAttribute('data-chart-rotated',false);if(!immersive&&frame&&getComputedStyle(frame).getPropertyValue('--chart-rotation').trim()==='1')current?.setAttribute('data-chart-rotated','true');};
  fitOverlay();window.addEventListener('resize',fitOverlay);
  if(immersive)document.body.style.overflow='hidden';
  current?.showModal();
  return()=>{window.removeEventListener('resize',fitOverlay);current?.close();if(immersive)document.body.style.overflow=overflow;previous?.focus();};
 },[immersive]);
 return <dialog ref={dialog} className={`analysis-modal${wide?' analysis-modal-wide':''}${immersive?' analysis-modal-immersive':''}`} aria-labelledby={titleId} onCancel={event=>{event.preventDefault();close.current();}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close.current();}}} onClick={event=>{if(event.target===event.currentTarget){const box=event.currentTarget.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)close.current();}}}>
  <div className={immersive&&mobile?'analysis-landscape-frame':undefined} style={!immersive||!mobile?{display:'contents'}:undefined} data-chart-frame={immersive&&mobile?'':undefined}>
  <header className="dialog-header"><h2 id={titleId}>{title}{immersive&&mobile?<small className="landscape-mode-label">横屏模式</small>:null}</h2><button type="button" className="icon-btn" aria-label="关闭弹窗" onClick={onClose}><svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
  <div className="analysis-modal-body">{children}</div>
  </div>
 </dialog>;
}
