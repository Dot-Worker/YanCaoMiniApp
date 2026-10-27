const test=require('node:test');
const assert=require('node:assert/strict');
const catalog=require('../miniprogram/utils/catalog');
const {database:db}=catalog;

test('frozen coverage is explicit and incomplete',()=>{
  assert.equal(db.products.length,82);assert.equal(db.brands.length,522);
  assert.equal(db.stats.indexedRelationships,333);assert.equal(db.stats.unreadIndexedRelationships,251);
  assert.equal(db.wholeWebsiteComplete,false);assert.equal(db.previewOnly,true);
  assert.equal(db.manufacturerVerified,false);
});
test('directory relations, not substring guessing, supply exact brand membership',()=>{
  assert.equal(catalog.searchProducts('','13').length,56);
  assert.equal(catalog.searchProducts('','131').length,26);
  assert.deepEqual(catalog.getProduct('5914').brandIds,['13']);
  assert.equal(catalog.getProduct('5914').name,'Chunghwa (Thailand)');
  for(const p of db.products) assert.deepEqual(p.brandIds.slice().sort(),p.brandEvidence.map(m=>m.brand_source_id).sort());
});
test('unobserved category, English name, and product total are not invented',()=>{
  const unknown=db.brands.filter(b=>b.availableCount===0);
  assert.equal(unknown.length,520);
  for(const b of unknown){assert.equal(b.listingSummary,null);assert.equal(b.sourceGroups,null);assert.equal(b.englishName,null);}
});
test('Chinese brand search and availability filter',()=>{
  assert.equal(catalog.searchBrands('中华',true).length,1);
  assert.equal(catalog.searchBrands('',true).length,2);
  assert.equal(catalog.searchBrands('',false).length,522);
});
test('English brand lookup is case insensitive without invented translation',()=>{
  assert.equal(catalog.searchBrands('american spirit',false)[0].name,'AMERICAN SPIRIT');
  assert.equal(catalog.searchBrands('AmErIcAn SpIrIt',false).length,1);
  assert.equal(catalog.searchBrands('american spirit',true).length,0);
});
test('English product lookup, tokenized whitespace, and brand association',()=>{
  assert.equal(catalog.searchProducts('CHUNGHWA')[0].id,'5914');
  assert.equal(catalog.searchProducts('  chunghwa   thailand ')[0].id,'5914');
  assert.ok(catalog.searchProducts('中华').some(p=>p.id==='5914'));
});
test('empty result, whitespace query, and clear are distinct and safe',()=>{
  assert.equal(catalog.searchProducts('no-such-record-0f91').length,0);
  assert.equal(catalog.searchProducts('   ').length,82);
  assert.equal(catalog.searchProducts('').length,82);
  assert.equal(catalog.searchProducts('', 'not-a-brand').length,0);
});
test('zero is a numeric value, never missing',()=>{
  const row=catalog.fieldView('tar_mg',{value:0,raw_unit:'mg',verification_status:'source_reported'});
  assert.equal(row.value,'0');assert.equal(row.present,true);assert.equal(row.unit,'mg');
});
test('null, absent, and needs-review fields cannot become zero or fabricated values',()=>{
  assert.equal(catalog.fieldView('tar_mg',{value:null,verification_status:'needs_review'}).value,'待核验');
  assert.equal(catalog.fieldView('tar_mg',{value:null,verification_status:'missing'}).value,'未采集');
  assert.equal(catalog.fieldView('tar_mg',{}).present,false);
  assert.equal(catalog.fieldView('tar_mg',{value:null,expected_unit:'mg'}).unit,'');
});
test('unit inferred from field context is visibly differentiated',()=>{
  const row=catalog.fieldView('sticks_per_pack',{value:20,raw_unit:null,expected_unit:'支',verification_status:'manually_read_image'});
  assert.equal(row.unit,'支');assert.match(row.note,/单位来自字段语境/);
});
test('multiple barcode records retain both source pack variants',()=>{
  const original=db.products[0];
  const sample=JSON.parse(JSON.stringify(original));sample.id='99999999';sample.barcodes=[{kind:'pack',value:'00111'},{kind:'pack',value:'00222'},{kind:'carton',value:'00333'}];db.products.push(sample);
  try {const v=catalog.productView(sample.id);assert.equal(v.hasMultiplePackBarcodes,true);assert.deepEqual(v.barcodes.map(b=>b.value),['00111','00222','00333']);assert.match(v.barcodes[1].label,/版本 2/);}
  finally {db.products.pop();}
});
test('missing image has no network fallback and does not hide product text',()=>{
  const product=db.products.find(p=>!p.imagePath);const card=catalog.productCard(product);
  assert.equal(card.imagePath,'');assert.ok(card.name);assert.ok(card.brandLabel);
});
test('unknown product IDs yield no fabricated detail',()=>{
  for(const id of ['',null,'non-numeric','999999999']) assert.equal(catalog.productView(id),null);
});
test('pagination covers exact boundaries without duplicate records',()=>{
  const all=catalog.searchBrands('',false);const first=catalog.paginate(all,40);const last=catalog.paginate(all,560);
  assert.equal(first.items.length,40);assert.equal(first.hasMore,true);assert.equal(last.items.length,522);assert.equal(last.hasMore,false);
  assert.equal(new Set(last.items.map(x=>x.id)).size,522);
});
test('no numeric values propagated across 81 other products',()=>{
  for(const p of db.products.filter(p=>p.id!=='610')){
    for(const m of Object.values(p.measurements)) assert.equal(m.value,null);
    for(const b of p.barcodes) assert.equal(b.value,null);
    assert.equal(p.imagePath,'');assert.deepEqual(p.evidence,[]);
  }
});
test('image-enabled snapshot retains exact 610 readings and all three barcodes',()=>{
  const p=catalog.getProduct('610');
  if(db.stats.localImages===0){assert.equal(p.imagePath,'');return;}
  assert.equal(db.stats.localImages,9);assert.equal(db.stats.productsWithImage,1);assert.equal(p.evidence.length,8);
  assert.deepEqual(p.barcodes.map(b=>b.value),['6901028184243','6901028179058','6901028184250']);
  assert.deepEqual(Object.fromEntries(Object.entries(p.measurements).map(([k,v])=>[k,v.value])),{tar_mg:10,nicotine_mg:1,carbon_monoxide_mg:10,length_mm:84,circumference_mm:null,filter_length_mm:null,sticks_per_pack:20,packs_per_carton:10});
  assert.equal(catalog.searchProducts('6901028179058')[0].id,'610');
  for(const field of [...Object.values(p.measurements),...p.barcodes].filter(f=>f.value!==null)){
    assert.equal(field.review_provenance.source_product_id,'610');assert.equal(field.evidence_paths.length,1);assert.equal(field.image_review,undefined);assert.match(field.review_provenance.image_review_sha256,/^[a-f0-9]{64}$/);
  }
});
