"""Evidence-only brand identities/memberships, independent of crawler jobs.

Original sanitized field JSON is retained as bounded, schema-checked evidence so
its exact byte hashes and JSON pointers can be verified after checkpoint restore.
"""
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from .policy import PolicyError, canonical_url
from .snapshot_import import _text
from .store import dump_json

MAX_DOCUMENT_BYTES = 128 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def blocked():
    raise PolicyError('Catalog evidence is outside the strict reviewed contract')


def strict_object(pairs):
    value={}
    for key,child in pairs:
        if key in value:blocked()
        value[key]=child
    return value


def strict_json(raw):
    try:return json.loads(raw,object_pairs_hook=strict_object,parse_constant=lambda _:blocked())
    except (ValueError,UnicodeError,RecursionError):blocked()


def checked_path(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):blocked()
    return path


def read_evidence_file(path):
    path=checked_path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):blocked()
        raw=f.read(MAX_DOCUMENT_BYTES+1)
    if len(raw)>MAX_DOCUMENT_BYTES:blocked()
    try:text=raw.decode('utf-8')
    except UnicodeError:blocked()
    return text


def shape(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):blocked()


def integer(value,low=0,high=10000):
    if type(value) is not int or not low<=value<=high:blocked()
    return value


def observed(value,precision):
    patterns={'day':r'\d{4}-\d{2}-\d{2}', 'millisecond':r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z'}
    if not isinstance(value,str) or not re.fullmatch(patterns[precision],value):blocked()
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if precision=='day':stamp=stamp.replace(tzinfo=timezone.utc)
        if stamp>datetime.now(timezone.utc):blocked()
    except ValueError:blocked()
    return value  # Preserve original spelling/precision; never invent midnight.


def link(value,kind):
    if not isinstance(value,str) or not re.fullmatch(r'https://www\.yanyue\.cn/'+kind+r'/[1-9][0-9]{0,12}',value) or canonical_url(value)!=value:blocked()
    return value.rsplit('/',1)[1]


def plain(value,*,allow_empty=False):
    _text(value,allow_empty=allow_empty)
    try:value.encode('utf-8')
    except UnicodeError:blocked()
    return value


def items(value,kind):
    if not isinstance(value,list) or len(value)>5000:blocked()
    result={}
    for pos,item in enumerate(value):
        shape(item,('name','url'));plain(item['name'])
        identity=link(item['url'],kind)
        if identity in result:blocked()
        result[identity]={'name':item['name'],'url':item['url'],'position':pos}
    return result


def parse_document(raw,kind):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>MAX_DOCUMENT_BYTES:blocked()
    value=strict_json(raw)
    if kind=='brand_directory':
        shape(value,('source_url','observed_at','scope','link_count','unique_brands'))
        if value['source_url']!='https://www.yanyue.cn/tobacco' or value['scope']!='traditional_tobacco_brand_directory_only':blocked()
        rows=items(value['unique_brands'],'sort');integer(value['link_count'])
        if not rows or value['link_count']<len(rows):blocked()
        return {'key':'directory','kind':kind,'source_url':value['source_url'],'observed_at':observed(value['observed_at'],'millisecond'),'time_precision':'millisecond','brands':rows,'observed_link_count':value['link_count']}
    if kind=='brand_index':
        shape(value,('observed_date','brand_id','method','scope','expected_records','pages','products'))
        if value['method']!='current_cloud_browser_rendered_dom' or value['scope']!='single_brand_index_only':blocked()
        brand=str(integer(value['brand_id'],1,10**13-1));rows=items(value['products'],'product')
        if integer(value['expected_records'])!=len(rows):blocked()
        return {'key':'brand-index:'+brand,'kind':kind,'brand_id':brand,'source_url':None,
                'observed_at':observed(value['observed_date'],'day'),'time_precision':'day','products':rows,
                'operator_reported_expected_records':value['expected_records'],'operator_reported_pages_observed':integer(value['pages'],1,1000)}
    if kind!='listing_page':blocked()
    shape(value,('source_url','observed_at','brand_id','total_text','products','pagination'))
    brand=str(integer(value['brand_id'],1,10**13-1))
    url=value['source_url']
    if not isinstance(url,str) or canonical_url(url)!=url:blocked()
    route=re.fullmatch(r'https://www\.yanyue\.cn/sort/(?:([1-9][0-9]*)|sort/sortid/([1-9][0-9]*)/p/([1-9][0-9]*))',url)
    if not route or (route[1] or route[2])!=brand:blocked()
    page=int(route[3] or '1')
    if not isinstance(value['total_text'],str):blocked()
    counts=re.fullmatch(r'共\s*([0-9]+)\s*条记录\s*[,，]\s*([0-9]+)/([0-9]+)\s*页',value['total_text'])
    if not counts or int(counts[2])!=page or not 1<=page<=int(counts[3])<=1000:blocked()
    count=integer(int(counts[1]));rows=items(value['products'],'product')
    if len(rows)>count:blocked()
    if not isinstance(value['pagination'],list) or len(value['pagination'])>100:blocked()
    for entry in value['pagination']:
        shape(entry,('text','url'));plain(entry['text'])
        target=canonical_url(entry['url'])
        if target!='https://www.yanyue.cn/sort/'+brand and not re.fullmatch(r'https://www\.yanyue\.cn/sort/sort/sortid/'+brand+r'/p/[1-9][0-9]*',target):blocked()
        # Safe observed page-one aliases remain exact in raw JSON, never replayed.
        if not re.fullmatch(r'https://www\.yanyue\.cn/sort/(?:'+brand+r'|sort/sortid/'+brand+r'/p/[1-9][0-9]*)',entry['url']):blocked()
    return {'key':f'listing-page:{brand}:{page}','kind':kind,'brand_id':brand,'source_url':url,
            'observed_at':observed(value['observed_at'],'millisecond'),'time_precision':'millisecond',
            'page':page,'reported_product_count':count,'reported_page_count':int(counts[3]),'products':rows}


def make_evidence(directory,brand_indices,listing_pages):
    if len(brand_indices)+len(listing_pages)+1>100:blocked()
    documents=[];total_bytes=0
    for kind,paths in (('brand_directory',[directory]),('brand_index',brand_indices),('listing_page',listing_pages)):
        for path in paths:
            raw=read_evidence_file(path);total_bytes+=len(raw.encode('utf-8'))
            if total_bytes>MAX_EVIDENCE_BYTES:blocked()
            parsed=parse_document(raw,kind)
            documents.append({'key':parsed['key'],'format':kind,'sha256':sha256(raw.encode()).hexdigest(),'raw_json':raw})
    documents.sort(key=lambda d:d['key'])
    return {'schema_version':'catalog_evidence_v1','current_source_groups_status':'not_collected_in_current_flat_directory','documents':documents}


def derive_catalog(products,evidence):
    shape(evidence,('schema_version','current_source_groups_status','documents'))
    if evidence['schema_version']!='catalog_evidence_v1' or evidence['current_source_groups_status']!='not_collected_in_current_flat_directory':blocked()
    documents=evidence['documents']
    if not isinstance(documents,list) or not 1<=len(documents)<=100:blocked()
    parsed={};total=0
    for document in documents:
        shape(document,('key','format','sha256','raw_json'))
        raw=document['raw_json']
        if not isinstance(raw,str):blocked()
        encoded=raw.encode('utf-8');total+=len(encoded)
        if total>MAX_EVIDENCE_BYTES or sha256(encoded).hexdigest()!=document['sha256']:blocked()
        source=parse_document(raw,document['format'])
        if document['key']!=source['key'] or source['key'] in parsed:blocked()
        source['sha256']=document['sha256'];parsed[source['key']]=source
    if list(parsed)!=sorted(parsed) or 'directory' not in parsed:blocked()
    directory=parsed['directory'];brands={}
    identities={}
    for sid,item in directory['brands'].items():
        brands[sid]={'source_id':sid,'name':item['name'],'source_url':item['url'],'source_groups':[]}
        identities[sid]={'source_key':'directory','input_sha256':directory['sha256'],'json_pointer':f'/unique_brands/{item["position"]}',
                         'source_url':directory['source_url'],'observed_at':directory['observed_at'],'time_precision':directory['time_precision']}
    by_brand=defaultdict(list);listed=defaultdict(dict);evidence_by_pair=defaultdict(list)
    for key,source in parsed.items():
        if source['kind']=='brand_directory':continue
        brand=source['brand_id']
        if brand not in brands:blocked()
        by_brand[brand].append(source)
        for pid,item in source['products'].items():
            old=listed[brand].get(pid)
            if old and old['name']!=item['name']:blocked()
            listed[brand][pid]=item
            evidence_by_pair[(brand,pid)].append({'source_key':key,'input_sha256':source['sha256'],
                'brand_id_pointer':'/brand_id','product_pointer':f'/products/{item["position"]}',
                'source_url':source['source_url'],'observed_at':source['observed_at'],'time_precision':source['time_precision'],
                'canonical_brand_source_url':brands[brand]['source_url'],'original_html_sha256':None})
    summaries={}
    for brand,sources in by_brand.items():
        kinds={s['kind'] for s in sources}
        if len(kinds)!=1:blocked()
        summary={'brand_source_id':brand,'unique_indexed_products':len(listed[brand]),'source_reported_product_count':None,
                 'source_reported_page_count':None,'operator_reported_expected_records':None,'operator_reported_pages_observed':None,'observed_listing_pages':None}
        if kinds=={'brand_index'}:
            if len(sources)!=1:blocked()
            summary.update(operator_reported_expected_records=sources[0]['operator_reported_expected_records'],operator_reported_pages_observed=sources[0]['operator_reported_pages_observed'])
        else:
            counts={(s['reported_product_count'],s['reported_page_count']) for s in sources}
            if len(counts)!=1:blocked()
            count,pages=next(iter(counts));observed_pages={s['page'] for s in sources}
            if len(observed_pages)!=len(sources) or len(listed[brand])>count:blocked()
            if len(observed_pages)==pages and (observed_pages!=set(range(1,pages+1)) or len(listed[brand])!=count):blocked()
            summary.update(source_reported_product_count=count,source_reported_page_count=pages,observed_listing_pages=len(observed_pages))
        summaries[brand]=summary
    memberships=[];seen_products=set()
    for product in products:
        pid=product['source_id']
        if pid in seen_products:blocked()
        seen_products.add(pid);found=False
        for brand,rows in listed.items():
            if pid not in rows:continue
            item=rows[pid]
            # Identity comes from links/explicit brand_id, never a name prefix.
            if product['source_url']!=item['url'] or product['name']!=item['name'] or product['fields']['brand_name']['value']!=brands[brand]['name']:blocked()
            found=True
            memberships.append({'parent_url':brands[brand]['source_url'],'child_url':product['source_url'],
                'brand_source_id':brand,'product_source_id':pid,'relation':'listed_under_brand',
                'listing_evidence':evidence_by_pair[(brand,pid)],'brand_identity_evidence':identities[brand],
                'detail_snapshot_sha256':product.get('snapshot_provenance',{}).get('input_sha256')})
        if not found:blocked()
    memberships.sort(key=lambda m:(int(m['brand_source_id']),int(m['product_source_id'])))
    for brand,summary in summaries.items():
        summary['available_detail_records']=sum(m['brand_source_id']==brand for m in memberships)
        summary['indexed_products_without_details']=summary['unique_indexed_products']-summary['available_detail_records']
    return {'brands':brands,'brand_identity_evidence':identities,'memberships':memberships,'listing_summaries':summaries,
            'directory_observed_link_count':directory['observed_link_count']}


def catalog_edges(value):
    evidence=value.get('catalog_evidence')
    if evidence is None:return set()
    graph=derive_catalog(value['products'],evidence)
    actual={b['source_id']:b for b in value['brands']}
    if len(actual)!=len(value['brands']) or actual!=graph['brands']:blocked()
    required={(m['parent_url'],m['child_url']) for m in graph['memberships']}
    present={(e['parent_url'],e['child_url']) for e in value['edges']}
    jobs={j['url'] for j in value.get('queue',[])}
    if len(present)!=len(value['edges']) or not required<=present or any(edge not in required and not set(edge)<=jobs for edge in present):blocked()
    return required


def install_catalog(store,evidence):
    products=[json.loads(r[0]) for r in store.db.execute('SELECT document FROM products ORDER BY source_id')]
    graph=derive_catalog(products,evidence)
    previous=store.meta('catalog_evidence')
    if previous is not None and previous!=evidence:raise PolicyError('Existing catalog evidence differs; reviewed reconciliation required')
    existing={r[0]:json.loads(r[1]) for r in store.db.execute('SELECT source_id,document FROM brands')}
    if any(graph['brands'].get(sid)!=brand for sid,brand in existing.items()):raise PolicyError('Existing brand identity/classification conflict')
    edges={(r[0],r[1]) for r in store.db.execute('SELECT parent_url,child_url FROM edges')}
    required={(m['parent_url'],m['child_url']) for m in graph['memberships']}
    jobs={r[0] for r in store.db.execute('SELECT url FROM queue')}
    if any(edge not in required and not set(edge)<=jobs for edge in edges):raise PolicyError('Unbound pre-existing edge requires review')
    if previous is not None:
        if existing!=graph['brands'] or not required<=edges:raise PolicyError('Existing catalog state is incomplete or changed')
        return {'already_imported':True,'brands':len(existing),'memberships':len(required),'source_jobs_created':0}
    with store.db:
        for sid,brand in graph['brands'].items():
            store.db.execute('INSERT OR IGNORE INTO brands VALUES (?,?)',(sid,dump_json(brand)))
        store.db.executemany('INSERT OR IGNORE INTO edges VALUES (?,?)',sorted(required))
        store.db.execute('INSERT INTO metadata VALUES (?,?)',('catalog_evidence',dump_json(evidence)))
    return {'already_imported':False,'brands':len(graph['brands']),'memberships':len(required),'source_jobs_created':0}


def export_view(store,*,brand_id=None,text=''):
    evidence=store.meta('catalog_evidence')
    products=[json.loads(r[0]) for r in store.db.execute('SELECT document FROM products ORDER BY source_id')]
    graph=derive_catalog(products,evidence)
    # Refuse a database edited independently of its verifiable source documents.
    document={'products':products,'brands':[json.loads(r[0]) for r in store.db.execute('SELECT document FROM brands')],
              'edges':[dict(r) for r in store.db.execute('SELECT parent_url,child_url FROM edges')],'queue':[{'url':r[0]} for r in store.db.execute('SELECT url FROM queue')],'catalog_evidence':evidence}
    catalog_edges(document)
    if brand_id is not None and brand_id not in graph['brands']:blocked()
    if not isinstance(text,str) or len(text)>100:blocked()
    plain(text,allow_empty=True)
    query=unicodedata.normalize('NFKC',text).casefold()
    links=defaultdict(list)
    for m in graph['memberships']:links[m['product_source_id']].append(m['brand_source_id'])
    summaries=[]
    for p in products:
        if brand_id is not None and brand_id not in links[p['source_id']]:continue
        if query not in unicodedata.normalize('NFKC',p['name']).casefold():continue
        summaries.append({'source_id':p['source_id'],'name':p['name'],'source_url':p['source_url'],'brand_source_ids':links[p['source_id']],
                          'observed_at':p['observed_at'],'detail_snapshot_sha256':p.get('snapshot_provenance',{}).get('input_sha256')})
    summaries.sort(key=lambda p:(p['name'].casefold(),int(p['source_id'])))
    brands=[]
    for sid,brand in graph['brands'].items():
        brands.append({**{k:brand[k] for k in ('source_id','name','source_url')},'source_groups':None,
            'source_groups_status':evidence['current_source_groups_status'],'identity_evidence':graph['brand_identity_evidence'][sid],
            'listing_summary':graph['listing_summaries'].get(sid),'available_detail_records':sum(sid in b for b in links.values())})
    brands.sort(key=lambda b:(b['name'].casefold(),int(b['source_id'])))
    return {'schema_version':'neutral_catalog_view_v1','brands':brands,'products':summaries,'memberships':[m for m in graph['memberships'] if m['product_source_id'] in {p['source_id'] for p in summaries}],
            'counts':{'directory_brand_records':len(brands),'available_detail_records':len(products),'matching_detail_records':len(summaries),'known_listing_brands':len(graph['listing_summaries']),
                      'indexed_relationships':sum(s['unique_indexed_products'] for s in graph['listing_summaries'].values()),'indexed_relationships_without_details':sum(s['indexed_products_without_details'] for s in graph['listing_summaries'].values())},
            'content_authorization':dict(products[0]['authorization']) if products else None,
            'source_contributor_type':'user_contributed_not_manufacturer_certified',
            'filter':{'brand_source_id':brand_id,'text':text},'sort':'name_then_source_id_not_a_recommendation',
            'whole_website_complete':False,'field_review_complete':False,'publication_ready':False,'manufacturer_verified':False}
