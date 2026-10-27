Component({
  properties:{ product:{type:Object,value:{}} },
  data:{ imageFailed:false },
  observers:{ 'product.imagePath':function(){this.setData({imageFailed:false});} },
  methods:{ imageError(){this.setData({imageFailed:true});}, select(){this.triggerEvent('select',{id:this.data.product.id});} }
});
