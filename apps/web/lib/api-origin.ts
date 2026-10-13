/** Validate the configured API origin without exposing its contents in errors. */
export function apiOrigin(value:string|undefined,hosted:boolean):string {
 if(value===undefined)return 'http://127.0.0.1:8000'; // Existing local/layout-preview contract.
 const invalid=()=>new Error('API_BASE_URL must be a valid API origin; hosted deployments require a public HTTPS origin');
 if(!value||value!==value.trim())throw invalid();
 let url:URL;try{url=new URL(value);}catch{throw invalid();}
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw invalid();
 const host=url.hostname.toLowerCase().replace(/^\[|\]$/g,'').replace(/\.$/,'');
 if(hosted&&(url.protocol!=='https:'||host==='localhost'||host.endsWith('.localhost')||host.endsWith('.local')||host==='::1'||host.includes(':')||/^\d+\.\d+\.\d+\.\d+$/.test(host)||!host.includes('.')))throw invalid();
 return url.origin;
}
