const catalog=require('../../utils/catalog');
const store=require('../../utils/storage');
const nav=require('../../utils/navigation');
Page({
  data:{query:'',brandId:'',brandName:'全部已收录资料',invalidBrand:false,items:[],total:0,hasMore:false,history:[],storageError:false},
  onLoad(options){
    const id=options && options.brandId || '';
    const brand=id ? catalog.getBrand(id) : null;
    this._limit=20;
    this.setData({brandId:id,brandName:brand ? brand.name : (id ? '未知品牌' : '全部已收录资料'),invalidBrand:!!id && !brand});
    this.refresh();this.loadHistory();
  },
  onShow(){nav.reset(this);},
  refresh(){const results=this.data.invalidBrand ? [] : catalog.searchProducts(this.data.query,this.data.brandId);this.setData(catalog.paginate(results,this._limit));},
  loadHistory(){const state=store.history(wx);this.setData({history:state.value,storageError:state.error});},
  onInput(e){this._limit=20;this.setData({query:e.detail.value});this.refresh();},
  search(){this._limit=20;this.refresh();const result=store.addHistory(wx,this.data.query);if(result.error)this.setData({storageError:true});else this.setData({history:result.value,storageError:false});},
  useHistory(e){this.setData({query:e.currentTarget.dataset.query});this.search();},
  clearSearch(){this._limit=20;this.setData({query:''});this.refresh();},
  clearHistory(){const result=store.clearHistory(wx);this.setData(result.error ? {storageError:true} : {history:[],storageError:false});},
  showAll(){this._limit=20;this.setData({brandId:'',brandName:'全部已收录资料',query:'',invalidBrand:false});this.refresh();},
  loadMore(){if(this.data.hasMore){this._limit+=20;this.refresh();}},
  onReachBottom(){this.loadMore();},
  openProduct(e){nav.open(this,wx,nav.detailUrl(e.detail.id));}
});
