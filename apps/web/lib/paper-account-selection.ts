import {z} from 'zod';

export const PaperSelectionKey='fc666.paper.stream.v1';
export type AccountIdentity={id:string;snapshot:string};

// A tab with its own selection never adopts another tab's recent account.
export function restoredPaperAccount(tabValue:string|null,recentValue:string|null):string|null{
 const candidate=tabValue??recentValue;
 return z.uuid().safeParse(candidate).success?candidate:null;
}

export function samePaperAccount(current:AccountIdentity|null,target:AccountIdentity):boolean{
 return current!==null&&current.id===target.id&&current.snapshot===target.snapshot;
}

export function stopConfirmationReady(target:AccountIdentity,confirmation:string):boolean{
 return z.uuid().safeParse(target.id).success&&/^[0-9a-f]{64}$/.test(target.snapshot)&&confirmation.trim().toLowerCase()===target.id.slice(-4);
}
