function detailUrl(id) { return /^[0-9]+$/.test(String(id)) ? '/pages/detail/detail?id='+encodeURIComponent(String(id)) : null; }
function productsUrl(brandId) { return '/pages/products/products'+(/^[0-9]+$/.test(String(brandId)) ? '?brandId='+encodeURIComponent(String(brandId)) : ''); }
function open(page,api,url) {
  if (!url || page._navigating) return false;
  page._navigating=true;
  api.navigateTo({url:url,fail:()=>{page._navigating=false;api.showToast({title:'页面暂时无法打开',icon:'none'});}});
  return true;
}
function reset(page) { page._navigating=false; }
function back(api,pages) { if(pages && pages.length>1) api.navigateBack({delta:1,fail:()=>api.switchTab({url:'/pages/home/home'})}); else api.switchTab({url:'/pages/home/home'}); }
module.exports={detailUrl,productsUrl,open,reset,back};
