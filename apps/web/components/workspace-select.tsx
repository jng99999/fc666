'use client';
import {useEffect,useId,useRef,useState,type KeyboardEvent} from 'react';
import {createPortal} from 'react-dom';
import './workspace-select.css';

type Option={value:string;label:string;disabled?:boolean};
type Props={label:string;value:string|number;onValueChange:(value:string)=>void;options:readonly Option[];disabled?:boolean;placeholder?:string;className?:string;id?:string};

export default function WorkspaceSelect({label,value,onValueChange,options,disabled=false,placeholder='请选择',className='',id}:Props){
 const generatedId=useId(),dialogId=generatedId+'-dialog',titleId=generatedId+'-title';
 const trigger=useRef<HTMLButtonElement>(null),dialog=useRef<HTMLDialogElement>(null),search=useRef<HTMLInputElement>(null);
 const [ready,setReady]=useState(false),[open,setOpen]=useState(false),[query,setQuery]=useState('');
 const current=options.find(option=>option.value===String(value));
 const normalized=query.trim().toLocaleLowerCase();
 const filtered=options.filter(option=>!normalized||option.label.toLocaleLowerCase().includes(normalized)||option.value.toLocaleLowerCase().includes(normalized));
 useEffect(()=>setReady(true),[]);
 useEffect(()=>{
  if(!open||!ready)return;
  const node=dialog.current;if(!node)return;
  node.showModal();const previous=document.body.style.overflow;document.body.style.overflow='hidden';
  const frame=requestAnimationFrame(()=>search.current?.focus());
  return()=>{cancelAnimationFrame(frame);node.close();document.body.style.overflow=previous;trigger.current?.focus();};
 },[open,ready]);
 useEffect(()=>{if(disabled)setOpen(false);},[disabled]);
 function close(){setOpen(false);}
 function choose(option:Option){if(option.disabled)return;onValueChange(option.value);close();}
 function keyboard(event:KeyboardEvent<HTMLDialogElement>){
  const node=dialog.current;if(!node)return;
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return;}
  const controls=Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled)')).filter(element=>element.getClientRects().length>0);
  if(event.key==='Tab'&&controls.length){const first=controls[0],last=controls[controls.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}return;}
  const choices=Array.from(node.querySelectorAll<HTMLButtonElement>('button[role=option]:not(:disabled)'));
  if(!choices.length)return;
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){
   const index=choices.indexOf(document.activeElement as HTMLButtonElement),direction=event.key==='ArrowDown'?1:-1;
   event.preventDefault();choices[index<0?(direction===1?0:choices.length-1):(index+direction+choices.length)%choices.length].focus();
  }else if((event.key==='Home'||event.key==='End')&&document.activeElement!==search.current){event.preventDefault();choices[event.key==='Home'?0:choices.length-1].focus();}
  else if(event.key==='Enter'&&document.activeElement===search.current){event.preventDefault();if(filtered.filter(option=>!option.disabled).length===1)choose(filtered.find(option=>!option.disabled)!);else choices[0].focus();}
 }
 return <><button ref={trigger} id={id} type="button" className={`workspace-select-trigger ${className}`} aria-label={label} aria-haspopup="dialog" aria-expanded={open} aria-controls={ready?dialogId:undefined} disabled={disabled||!ready} title={current?.label} onClick={()=>{setQuery('');setOpen(true);}}><span>{current?.label??placeholder}</span><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
 {ready?createPortal(<dialog ref={dialog} id={dialogId} className="workspace-select-dialog" aria-labelledby={titleId} onCancel={event=>{event.preventDefault();close();}} onClose={close} onKeyDown={keyboard} onClick={event=>{if(event.target===event.currentTarget){const box=event.currentTarget.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)close();}}}>
  <header><h2 id={titleId}>{label}</h2><button type="button" className="workspace-select-close" aria-label={'关闭'+label} onClick={close}><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></header>
  <div className="workspace-select-search"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input ref={search} type="search" aria-label={'搜索'+label} value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索选项" autoComplete="off"/>{query?<button type="button" onClick={()=>{setQuery('');search.current?.focus();}}>清空</button>:null}</div>
  <div className="workspace-select-options" role="listbox" aria-label={label+'选项'}>{filtered.length?filtered.map(option=><button key={option.value} type="button" role="option" aria-selected={option.value===String(value)} disabled={option.disabled} onClick={()=>choose(option)}><span>{option.label}</span><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 12 4 4 10-10"/></svg></button>):<p role="status">没有匹配选项，请更换关键词。</p>}</div>
  <footer><span>{filtered.length} 个选项</span><button type="button" onClick={close}>取消</button></footer>
 </dialog>,document.body):null}</>;
}
