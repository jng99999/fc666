import {build} from '../apps/web/node_modules/esbuild/lib/main.js';
import assert from 'node:assert/strict';
const compiled=await build({entryPoints:['apps/web/lib/api-origin.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {apiOrigin}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
let checks=0;
for(const [value,hosted,expected] of [[undefined,false,'http://127.0.0.1:8000'],[undefined,true,'http://127.0.0.1:8000'],['http://127.0.0.1:8000/',false,'http://127.0.0.1:8000'],['https://api.example.com/',true,'https://api.example.com'],['https://api.example.com:8443',true,'https://api.example.com:8443']]){assert.equal(apiOrigin(value,hosted),expected);checks++;}
for(const value of ['', ' https://api.example.com','ftp://api.example.com','https://user:secret@api.example.com','https://api.example.com/path','https://api.example.com?token=secret','https://api.example.com#secret','invalid']){assert.throws(()=>apiOrigin(value,false),error=>!error.message.includes('secret'));checks++;}
for(const value of ['http://api.example.com','https://localhost','https://localhost.','https://x.localhost','https://backend.local','https://127.0.0.1','https://10.0.0.1','https://[::1]','https://[2001:db8::1]','https://backend']){assert.throws(()=>apiOrigin(value,true));checks++;}
console.log(`API origin validation passed: ${checks} assertions`);
