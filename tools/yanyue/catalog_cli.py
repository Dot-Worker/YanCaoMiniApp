"""Strict offline catalog initialization/query/export; no browser or source jobs."""
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from .catalog import checked_path, make_evidence, derive_catalog, install_catalog, export_view, strict_json
from .checkpoint import restore_checkpoint
from .policy import PolicyError
from .schema import validate
from .store import Store, dump_json

BASE_CHECKPOINT_SHA256 = '7919b95519ae925f2c556bc2bc4a3ada4f26ede7571ec31428356750bafed517'


class ReadonlyStore:
    def __init__(self,path):
        self.root=checked_path(path)
        db=checked_path(self.root/'state.sqlite')
        if not db.is_file() or db.stat().st_nlink!=1 or db.stat().st_size>32*1024*1024:
            raise PolicyError('Catalog state must be a bounded private regular SQLite file')
        wal=checked_path(self.root/'state.sqlite-wal')
        if wal.exists() and wal.stat().st_size:raise PolicyError('Read-only catalog requires a quiescent database')
        self.db=sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
    def meta(self,key,default=None):
        row=self.db.execute('SELECT value FROM metadata WHERE key=? AND length(value)<=12000000',(key,)).fetchone()
        return strict_json(row[0]) if row else default
    def close(self):self.db.close()


def read_base(path):
    path=checked_path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise PolicyError('Checkpoint must be a regular file')
        raw=f.read(20*1024*1024+1)
    if len(raw)>20*1024*1024 or sha256(raw).hexdigest()!=BASE_CHECKPOINT_SHA256:
        raise PolicyError('Only the reviewed 82-product baseline is accepted')
    value=strict_json(raw);validate(value,'checkpoint.schema.json')
    if value['brands'] or value['edges'] or value['brand_reports'] or 'catalog_evidence' in value:
        raise PolicyError('Catalog initialization baseline is not empty of associations')
    return raw,value


def initialize(checkpoint,state_dir,evidence):
    raw,source=read_base(checkpoint)
    validate(evidence,'catalog-evidence.schema.json')
    graph=derive_catalog(source['products'],evidence)
    target=checked_path(state_dir)
    if target.exists():
        # Idempotency is read-only, never an opportunity to modify a prior state.
        old=ReadonlyStore(target)
        try:
            if old.meta('catalog_evidence')!=evidence:raise PolicyError('Existing state requires reviewed reconciliation')
            products=[json.loads(r[0]) for r in old.db.execute('SELECT document FROM products ORDER BY source_id')]
            if products!=sorted(source['products'],key=lambda p:p['source_id']):raise PolicyError('Existing product records differ')
            result=export_view(old)
            return {'already_imported':True,'network_executed':False,'source_jobs_created':0,'counts':result['counts']}
        finally:old.close()
    store=Store(target)
    try:
        with store.exclusive():
            # Restore the immutable captured bytes, not a mutable input path.
            with tempfile.TemporaryDirectory(prefix='yanyue-catalog-base-') as directory:
                captured=Path(directory)/'checkpoint.json';captured.write_bytes(raw)
                restore_checkpoint(store,captured)
            result=install_catalog(store,evidence)
            result.update(network_executed=False,counts=export_view(store)['counts'])
            return result
    finally:store.close()


def write_new_export(path,value,state_root):
    path=checked_path(path)
    if path.exists() or path.is_relative_to(state_root):raise PolicyError('Export requires a new path outside the state namespace')
    body=(dump_json(value)+'\n').encode()
    if len(body)>8*1024*1024:raise PolicyError('Catalog export exceeds fixed limit')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.catalog-',suffix='.tmp',dir=path.parent)
    temporary=Path(temporary)
    try:
        with os.fdopen(fd,'wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        os.link(temporary,path)  # Exclusive publish: never overwrite a raced-in file.
    finally:temporary.unlink(missing_ok=True)


def main(argv=None):
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            raise PolicyError('Invalid offline catalog arguments')
    p=SafeArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--checkpoint',required=True);init.add_argument('--state-dir',required=True)
    init.add_argument('--directory',required=True);init.add_argument('--brand-index',action='append',default=[])
    init.add_argument('--listing-page',action='append',default=[])
    for kind in ('query','export'):
        c=sub.add_parser(kind);c.add_argument('--state-dir',required=True);c.add_argument('--brand-id');c.add_argument('--text',default='')
        if kind=='export':c.add_argument('--output',required=True)
    store=None
    try:
        args=p.parse_args(argv)
        if args.command=='init':
            evidence=make_evidence(args.directory,args.brand_index,args.listing_page)
            result=initialize(args.checkpoint,args.state_dir,evidence)
        else:
            store=ReadonlyStore(args.state_dir);result=export_view(store,brand_id=args.brand_id,text=args.text)
            if args.command=='export':
                write_new_export(args.output,result,store.root)
                result={'exported':True,'network_executed':False,'counts':result['counts'],'whole_website_complete':False,'publication_ready':False}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));return 0
    except Exception:
        print(json.dumps({'error':'offline_catalog_contract_or_conflict','network_executed':False}));return 2
    finally:
        if store:store.close()

if __name__=='__main__':raise SystemExit(main())
