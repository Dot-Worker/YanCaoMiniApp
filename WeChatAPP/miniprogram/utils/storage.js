const FAVORITES='yancao.preview.favorites.v1';
const HISTORY='yancao.preview.searches.v1';
function cleanIds(value, allowed) {
  if (!Array.isArray(value)) return [];
  return value.filter((v,i,a)=>typeof v === 'string' && /^[0-9]+$/.test(v) && a.indexOf(v) === i && (!allowed || allowed.indexOf(v)!==-1));
}
function cleanHistory(value) {
  if (!Array.isArray(value)) return [];
  const out=[];
  value.forEach(v=>{ if(typeof v !== 'string') return; const q=v.trim().slice(0,80); if(q && !out.some(x=>x.toLowerCase()===q.toLowerCase())) out.push(q); });
  return out.slice(0,6);
}
function read(api,key,clean) { try { return {value:clean(api.getStorageSync(key)),error:false}; } catch(e) { return {value:[],error:true}; } }
function write(api,key,value) { try { api.setStorageSync(key,value); return {value:value,error:false}; } catch(e) { return {value:null,error:true}; } }
function favorites(api,allowed) { return read(api,FAVORITES,v=>cleanIds(v,allowed)); }
function toggleFavorite(api,id,allowed) {
  if (typeof id !== 'string' || !/^[0-9]+$/.test(id) || (allowed && allowed.indexOf(id)===-1)) return {value:null,error:true};
  const old=favorites(api,allowed); if(old.error) return {value:null,error:true};
  const next=old.value.indexOf(id)===-1 ? old.value.concat(id) : old.value.filter(v=>v!==id);
  return write(api,FAVORITES,next);
}
function history(api) { return read(api,HISTORY,cleanHistory); }
function addHistory(api,query) {
  const old=history(api); if(old.error) return {value:null,error:true};
  if(typeof query !== 'string' || !query.trim()) return old;
  return write(api,HISTORY,cleanHistory([query].concat(old.value)));
}
function clearHistory(api) { return write(api,HISTORY,[]); }
module.exports={FAVORITES,HISTORY,cleanIds,cleanHistory,favorites,toggleFavorite,history,addHistory,clearHistory};
