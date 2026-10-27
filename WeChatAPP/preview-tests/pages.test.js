const test=require('node:test');const assert=require('node:assert/strict');const path=require('node:path');
const nav=require('../miniprogram/utils/navigation');const store=require('../miniprogram/utils/storage');
function api(){const data={};const calls=[];return {data,calls,getStorageSync(k){return data[k];},setStorageSync(k,v){data[k]=v;},navigateTo(o){calls.push(['navigateTo',o]);},navigateBack(o){calls.push(['navigateBack',o]);},switchTab(o){calls.push(['switchTab',o]);},showToast(o){calls.push(['showToast',o]);}};}
function page(name){let definition;global.Page=d=>{definition=d;};const file=path.resolve(__dirname,'../miniprogram/pages/'+name+'/'+name+'.js');delete require.cache[file];require(file);delete global.Page;const instance=Object.assign({},definition,{data:JSON.parse(JSON.stringify(definition.data))});instance.setData=function(d){Object.assign(this.data,d);};return instance;}
test('route URLs validate IDs and avoid accidental external navigation',()=>{assert.equal(nav.detailUrl('610'),'/pages/detail/detail?id=610');assert.equal(nav.detailUrl('1&bad=x'),null);assert.equal(nav.productsUrl('13'),'/pages/products/products?brandId=13');assert.equal(nav.productsUrl('http://bad'),'/pages/products/products');});
test('double taps cannot stack repeated page opens before onShow',()=>{const a=api(),p={};assert.equal(nav.open(p,a,'/pages/about/about'),true);assert.equal(nav.open(p,a,'/pages/about/about'),false);assert.equal(a.calls.length,1);nav.reset(p);assert.equal(nav.open(p,a,'/pages/about/about'),true);});
test('failed navigation unlocks retry and reports failure',()=>{const a=api(),p={};nav.open(p,a,'/pages/about/about');a.calls[0][1].fail();assert.equal(p._navigating,false);assert.equal(a.calls[1][0],'showToast');assert.equal(nav.open(p,a,'/pages/about/about'),true);});
test('back uses native history, with home fallback for direct entry',()=>{const a=api();nav.back(a,[{},{}]);assert.equal(a.calls[0][0],'navigateBack');a.calls[0][1].fail();assert.equal(a.calls[1][0],'switchTab');nav.back(a,[{}]);assert.equal(a.calls[2][0],'switchTab');});
test('brand page searches, toggles available filter, clears, and paginates all 522',()=>{
 global.wx=api();const p=page('brands');p.onLoad();assert.equal(p.data.total,2);p.toggleAvailable({detail:{value:false}});assert.equal(p.data.total,522);assert.equal(p.data.items.length,40);p.onInput({detail:{value:'aMeRiCaN sPiRiT'}});assert.equal(p.data.total,1);p.clearSearch();assert.equal(p.data.total,522);while(p.data.hasMore)p.loadMore();assert.equal(p.data.items.length,522);p.onShow();assert.equal(p._navigating,false);
});
test('product page search/clear/brand context is preserved on returning from detail',()=>{
 global.wx=api();const p=page('products');p.onLoad({brandId:'13'});assert.equal(p.data.total,56);p.onInput({detail:{value:'CHUNGHWA'}});assert.equal(p.data.total,1);p.search();assert.deepEqual(p.data.history,['CHUNGHWA']);p.openProduct({detail:{id:'5914'}});p.onShow();assert.equal(p.data.query,'CHUNGHWA');assert.equal(p.data.brandId,'13');assert.equal(p.data.total,1);p.clearSearch();assert.equal(p.data.total,56);p.showAll();assert.equal(p.data.total,82);
});
test('unknown brand does not silently display the entire database',()=>{
 global.wx=api();const p=page('products');p.onLoad({brandId:'999999999'});assert.equal(p.data.invalidBrand,true);assert.equal(p.data.total,0);p.showAll();assert.equal(p.data.invalidBrand,false);assert.equal(p.data.total,82);
});
test('known uncollected brand shows empty detail, not zero known products',()=>{
 global.wx=api();const p=page('products');p.onLoad({brandId:'399'});assert.equal(p.data.invalidBrand,false);assert.equal(p.data.brandName,'AMERICAN SPIRIT');assert.equal(p.data.total,0);
});
test('search history selection is repeatable, clear restores empty state',()=>{
 global.wx=api();const p=page('products');p.onLoad({});p.useHistory({currentTarget:{dataset:{query:'中华'}}});p.search();assert.deepEqual(p.data.history,['中华']);p.clearHistory();assert.deepEqual(p.data.history,[]);assert.deepEqual(wx.data[store.HISTORY],[]);
});
test('search remains functional when local persistence fails',()=>{
 global.wx=api();wx.getStorageSync=()=>{throw Error('no storage');};const p=page('products');p.onLoad({});assert.equal(p.data.storageError,true);p.onInput({detail:{value:'Chunghwa'}});p.search();assert.equal(p.data.total,1);assert.equal(p.data.storageError,true);
});
test('favorite state refreshes when returning to detail and saved tab',()=>{
 global.wx=api();const d=page('detail');d.onLoad({id:'610'});d.onShow();assert.equal(d.data.favorite,false);d.toggleFavorite();assert.equal(d.data.favorite,true);const s=page('saved');s.onShow();assert.equal(s.data.items.length,1);d.toggleFavorite();s.onShow();assert.equal(s.data.items.length,0);
});
test('favorite write failure does not display a false saved success',()=>{
 global.wx=api();const d=page('detail');d.onLoad({id:'610'});d.onShow();wx.setStorageSync=()=>{throw Error('quota');};d.toggleFavorite();assert.equal(d.data.favorite,false);assert.equal(d.data.storageError,true);
});
test('saved tab read failure is visible and retry can recover',()=>{
 global.wx=api();const original=wx.getStorageSync;wx.getStorageSync=()=>{throw Error('temporary');};const s=page('saved');s.onShow();assert.equal(s.data.storageError,true);wx.getStorageSync=original;s.retry();assert.equal(s.data.storageError,false);
});
test('invalid detail, image failure, and evidence disclosure have safe fallbacks',()=>{
 global.wx=api();global.getCurrentPages=()=>[{}];const d=page('detail');d.onLoad({id:'missing'});assert.equal(d.data.product,null);d.toggleFavorite();assert.deepEqual(wx.data,{});d.back();assert.equal(wx.calls[0][0],'switchTab');d.onLoad({id:'610'});d.imageError();assert.equal(d.data.imageFailed,true);d.toggleEvidence();assert.equal(d.data.evidenceOpen,true);d.toggleEvidence();assert.equal(d.data.evidenceOpen,false);d.evidenceError({currentTarget:{dataset:{key:'sha'}}});assert.equal(d.data.failedEvidence.sha,true);
});
test('product card error handling never installs a remote placeholder',()=>{
 let definition;global.Component=d=>{definition=d;};require('../miniprogram/components/product-card/product-card');delete global.Component;
 const c={data:{imageFailed:false,product:{id:'610'}},setData(d){Object.assign(this.data,d);},triggerEvent(name,detail){this.event={name,detail};}};
 definition.methods.imageError.call(c);assert.equal(c.data.imageFailed,true);definition.methods.select.call(c);assert.deepEqual(c.event,{name:'select',detail:{id:'610'}});definition.observers['product.imagePath'].call(c);assert.equal(c.data.imageFailed,false);
});
