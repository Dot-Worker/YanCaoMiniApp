const database = require('../data/catalog');
const MEASURES = ['tar_mg','nicotine_mg','carbon_monoxide_mg','length_mm','circumference_mm','filter_length_mm','sticks_per_pack','packs_per_carton'];
const TEXT_FIELDS = ['type','package_form','cigarette_format','primary_color','secondary_color'];
const LABELS = { tar_mg:'焦油量', nicotine_mg:'烟气烟碱量', carbon_monoxide_mg:'烟气一氧化碳量', length_mm:'烟支长度', circumference_mm:'烟支周长', filter_length_mm:'过滤嘴长度', sticks_per_pack:'每盒数量', packs_per_carton:'条装盒数', type:'类型', package_form:'包装形式', cigarette_format:'烟支', primary_color:'包装主色', secondary_color:'包装副色' };
function normalize(value) { return String(value == null ? '' : value).trim().toLowerCase().replace(/\s+/g,' '); }
function hasValue(value) { return value !== null && value !== undefined && value !== ''; }
function dateLabel(value) { return value ? String(value).slice(0,10) : '未记录'; }
function fieldView(key, field) {
  const item = field || {};
  const present = hasValue(item.value);
  const unit = present ? (item.raw_unit || item.expected_unit || '') : '';
  const reviewed = item.verification_status === 'manually_read_image';
  return { key:key, label:LABELS[key] || item.source_label || key, value:present ? String(item.value) : (item.verification_status === 'needs_review' ? '待核验' : '未采集'), unit:unit, present:present, note:reviewed ? (item.raw_unit ? '来源图像人工转录' : '来源图像人工转录 · 单位来自字段语境') : (present ? '来源文本记录' : (item.verification_status === 'needs_review' ? '数值图尚未转录，不推测' : '来源快照未提供')), evidencePaths:item.evidence_paths || [] };
}
function getBrand(id) { return database.brands.find(b=>b.id === String(id)); }
function getProduct(id) { return database.products.find(p=>p.id === String(id)); }
function productCard(product) {
  const brands = product.brandIds.map(getBrand).filter(Boolean);
  return { id:product.id, name:product.name, brandLabel:brands.map(b=>b.name).join(' / ') || '目录关系未核验', typeLabel:product.fields.type && product.fields.type.value || '类型未采集', imagePath:product.imagePath || '', dateLabel:dateLabel(product.observedAt) };
}
function searchProducts(query, brandId) {
  const terms = normalize(query).split(' ').filter(Boolean);
  return database.products.filter(p=>!brandId || p.brandIds.indexOf(String(brandId)) !== -1).filter(p=>{
    const haystack = normalize([p.name,p.id].concat(p.brandIds.map(id=>{ const b=getBrand(id); return b ? b.name : ''; }),p.barcodes.map(b=>b.value || '')).join(' '));
    return terms.every(term=>haystack.indexOf(term) !== -1);
  }).map(productCard);
}
function searchBrands(query, availableOnly) {
  const term = normalize(query);
  return database.brands.filter(b=>(!availableOnly || b.availableCount > 0) && normalize(b.name).indexOf(term) !== -1);
}
function productView(id) {
  const p=getProduct(id); if (!p) return null;
  const packCount=p.barcodes.filter(b=>b.kind === 'pack' && hasValue(b.value)).length;
  let packIndex=0;
  const barcodes=p.barcodes.map((b,index)=>{
    const present=hasValue(b.value);
    const version=b.kind === 'pack' && present ? ++packIndex : 0;
    return { key:'barcode-'+index, label:b.kind === 'carton' ? '条装条码' : (packCount>1 && present ? '小盒条码 · 来源所列版本 '+version : '小盒条码'), value:present ? String(b.value) : (b.verification_status === 'needs_review' ? '待核验' : '未采集'), present:present, note:present ? '来源图像人工转录' : (b.verification_status === 'needs_review' ? '条码图尚未转录' : '来源快照未提供'), evidencePaths:b.evidence_paths || [] };
  });
  return Object.assign({}, productCard(p), { sourceUrl:p.sourceUrl, observedAt:p.observedAt, fields:TEXT_FIELDS.map(k=>fieldView(k,p.fields[k])), measurements:MEASURES.map(k=>fieldView(k,p.measurements[k])), barcodes:barcodes, hasMultiplePackBarcodes:packCount>1, evidence:p.evidence || [], brandEvidence:p.brandEvidence || [] });
}
function paginate(items, count) { return {items:items.slice(0,count), total:items.length, hasMore:items.length>count}; }
module.exports={database,normalize,hasValue,dateLabel,fieldView,getBrand,getProduct,productCard,searchProducts,searchBrands,productView,paginate};
