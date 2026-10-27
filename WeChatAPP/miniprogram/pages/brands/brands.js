const catalog=require('../../utils/catalog');
const nav=require('../../utils/navigation');
Page({
  data:{query:'',availableOnly:true,items:[],total:0,hasMore:false,allCount:catalog.database.stats.brands},
  onLoad(){this._limit=40;this.refresh();},
  onShow(){nav.reset(this);},
  refresh(){this.setData(catalog.paginate(catalog.searchBrands(this.data.query,this.data.availableOnly),this._limit));},
  onInput(e){this._limit=40;this.setData({query:e.detail.value});this.refresh();},
  clearSearch(){this._limit=40;this.setData({query:''});this.refresh();},
  toggleAvailable(e){this._limit=40;this.setData({availableOnly:!!e.detail.value});this.refresh();},
  showAll(){this._limit=40;this.setData({query:'',availableOnly:false});this.refresh();},
  onReachBottom(){this.loadMore();},
  loadMore(){if(this.data.hasMore){this._limit+=40;this.refresh();}},
  openBrand(e){nav.open(this,wx,nav.productsUrl(e.currentTarget.dataset.id));}
});
