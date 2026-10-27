const test=require('node:test');const assert=require('node:assert/strict');const store=require('../miniprogram/utils/storage');
function memory(){const data={};return {data,getStorageSync(k){return data[k];},setStorageSync(k,v){data[k]=v;}};}
test('favorite IDs are validated, deduplicated and restricted to available records',()=>{
 assert.deepEqual(store.cleanIds(['610','610',610,'bad','13','82'],['610','13']),['610','13']);
 assert.deepEqual(store.cleanIds('610'),[]);
});
test('favorites toggle twice restores prior state',()=>{
 const api=memory();assert.deepEqual(store.toggleFavorite(api,'610',['610']).value,['610']);assert.deepEqual(store.toggleFavorite(api,'610',['610']).value,[]);
});
test('favorites do not accept nonexistent or malformed IDs',()=>{const api=memory();for(const id of ['invalid','888',{},610])assert.equal(store.toggleFavorite(api,id,['610']).error,true);assert.deepEqual(api.data,{});});
test('storage read exception never overwrites an unread existing favorite store',()=>{
 let writes=0;const api={getStorageSync(){throw Error('blocked');},setStorageSync(){writes++;}};
 assert.deepEqual(store.favorites(api),{value:[],error:true});assert.equal(store.toggleFavorite(api,'610').error,true);assert.equal(writes,0);
});
test('write exception is exposed and original store remains unchanged',()=>{
 const api=memory();api.data[store.FAVORITES]=['610'];api.setStorageSync=()=>{throw Error('quota');};const result=store.toggleFavorite(api,'610');assert.equal(result.error,true);assert.equal(result.value,null);assert.deepEqual(api.data[store.FAVORITES],['610']);
});
test('corrupt persisted data is treated safely, never executed',()=>{
 const api=memory();api.data[store.FAVORITES]={constructor:'bad'};api.data[store.HISTORY]=[null,{},'','  中华  '];assert.deepEqual(store.favorites(api).value,[]);assert.deepEqual(store.history(api).value,['中华']);
});
test('history is trimmed, case-insensitively deduplicated and capped at six',()=>{
 const api=memory();for(const q of ['a','b','c','d','e','f','g'])store.addHistory(api,q);assert.deepEqual(store.history(api).value,['g','f','e','d','c','b']);
 store.addHistory(api,'  F  ');assert.deepEqual(store.history(api).value,['F','g','e','d','c','b']);
});
test('empty history submissions do not clear or add entries',()=>{const api=memory();store.addHistory(api,'中华');store.addHistory(api,'  ');assert.deepEqual(store.history(api).value,['中华']);});
test('history clear persists empty state, failure remains visible',()=>{const api=memory();store.addHistory(api,'中华');assert.deepEqual(store.clearHistory(api).value,[]);assert.deepEqual(store.history(api).value,[]);api.setStorageSync=()=>{throw Error('blocked');};assert.equal(store.clearHistory(api).error,true);});
test('history read failure cannot be mistaken for successful save',()=>{const api={getStorageSync(){throw Error('read');},setStorageSync(){throw Error('should-not-write');}};assert.equal(store.addHistory(api,'中华').error,true);});
test('long history is bounded and malformed numeric input ignored',()=>{const api=memory();store.addHistory(api,'a'.repeat(150));store.addHistory(api,12);assert.equal(store.history(api).value[0].length,80);});
