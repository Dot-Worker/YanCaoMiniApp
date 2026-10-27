const catalog=require('../../utils/catalog');
const store=require('../../utils/storage');
const nav=require('../../utils/navigation');
Page({
  data:{items:[],storageError:false},
  onShow(){nav.reset(this);const result=store.favorites(wx,catalog.database.products.map(p=>p.id));this.setData({items:result.value.map(catalog.getProduct).filter(Boolean).map(catalog.productCard),storageError:result.error});},
  retry(){this.onShow();},
  openSearch(){nav.open(this,wx,nav.productsUrl());},
  openProduct(e){nav.open(this,wx,nav.detailUrl(e.detail.id));}
});
