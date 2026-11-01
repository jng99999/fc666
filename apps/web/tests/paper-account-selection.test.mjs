import {test} from 'node:test';
import assert from 'node:assert/strict';
import {restoredPaperAccount,samePaperAccount,stopConfirmationReady} from '../lib/paper-account-selection.ts';
const a='11111111-1111-4111-8111-111111111111',b='22222222-2222-4222-8222-222222222222';
const target={id:a,snapshot:'a'.repeat(64)};
test('each tab keeps its own account when another tab changes the recent selection',()=>{
 assert.equal(restoredPaperAccount(a,b),a);
 assert.equal(restoredPaperAccount(b,a),b);
 assert.equal(restoredPaperAccount(null,b),b);
});
test('corrupt tab identity does not fall back to another account',()=>{
 for(const value of ['', 'not-an-account'])assert.equal(restoredPaperAccount(value,b),null);
 assert.equal(restoredPaperAccount(null,'bad'),null);
 assert.equal(restoredPaperAccount(null,null),null);
});
test('stop target binds both account ID and frozen snapshot',()=>{
 assert.equal(samePaperAccount({...target},target),true);
 assert.equal(samePaperAccount({id:b,snapshot:target.snapshot},target),false);
 assert.equal(samePaperAccount({id:a,snapshot:'b'.repeat(64)},target),false);
 assert.equal(samePaperAccount(null,target),false);
});
test('stop requires the exact account suffix and a valid frozen identity',()=>{
 assert.equal(stopConfirmationReady(target,'1111'),true);
 for(const value of ['', '2222', '111', '11111'])assert.equal(stopConfirmationReady(target,value),false);
 assert.equal(stopConfirmationReady({...target,id:'1111'},'1111'),false);
 assert.equal(stopConfirmationReady({...target,snapshot:''},'1111'),false);
});
