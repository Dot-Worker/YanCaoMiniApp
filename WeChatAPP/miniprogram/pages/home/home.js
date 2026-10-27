const catalog=require('../../utils/catalog');
const nav=require('../../utils/navigation');
Page({
  data:{stats:catalog.database.stats,availableBrands:catalog.database.brands.filter(b=>b.availableCount>0),snapshotDate:catalog.database.snapshotDate},
  onShow(){nav.reset(this);},
  openSearch(){nav.open(this,wx,nav.productsUrl());},
  openBrand(e){nav.open(this,wx,nav.productsUrl(e.currentTarget.dataset.id));},
  openBrands(){wx.switchTab({url:'/pages/brands/brands'});},
  openAbout(){nav.open(this,wx,'/pages/about/about');}
});
