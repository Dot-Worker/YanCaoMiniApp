const catalog=require('../../utils/catalog');
const store=require('../../utils/storage');
const nav=require('../../utils/navigation');
Page({
  data:{product:null,favorite:false,storageError:false,imageFailed:false,evidenceOpen:false,failedEvidence:{}},
  onLoad(options){this._id=options && options.id || '';this.setData({product:catalog.productView(this._id)});},
  onShow(){nav.reset(this);const state=store.favorites(wx,catalog.database.products.map(p=>p.id));this.setData(state.error ? {storageError:true} : {favorite:state.value.indexOf(this._id)!==-1,storageError:false});},
  toggleFavorite(){if(!this.data.product)return;const result=store.toggleFavorite(wx,this._id,catalog.database.products.map(p=>p.id));if(result.error){this.setData({storageError:true});return;}this.setData({favorite:result.value.indexOf(this._id)!==-1,storageError:false});},
  imageError(){this.setData({imageFailed:true});},
  evidenceError(e){const failed=Object.assign({},this.data.failedEvidence);failed[e.currentTarget.dataset.key]=true;this.setData({failedEvidence:failed});},
  toggleEvidence(){this.setData({evidenceOpen:!this.data.evidenceOpen});},
  back(){nav.back(wx,getCurrentPages());},
  openAbout(){nav.open(this,wx,'/pages/about/about');}
});
