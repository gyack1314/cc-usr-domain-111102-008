import argparse
import json
import sys
from .io import inspect_bundle,read_json
from .catalog import Catalog
from .search import SearchStore
from .query import QueryError

def main():
    parser=argparse.ArgumentParser(description='业务资料读取、本地存档与位置倒排查询')
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('inspect');p.add_argument('directory')
    p=subs.add_parser('ingest');p.add_argument('directory');p.add_argument('database')
    p=subs.add_parser('catalog');p.add_argument('database')
    p=subs.add_parser('index');p.add_argument('documents');p.add_argument('database')
    p=subs.add_parser('query');p.add_argument('database');p.add_argument('expression')
    p=subs.add_parser('query-file');p.add_argument('database');p.add_argument('queries')
    p=subs.add_parser('delete');p.add_argument('database');p.add_argument('doc_id')
    p=subs.add_parser('verify');p.add_argument('database')
    args=parser.parse_args()
    try:
        if args.command=='inspect':result=inspect_bundle(args.directory)
        elif args.command=='ingest':result={'import_id':Catalog(args.database).ingest(args.directory)}
        elif args.command=='catalog':result=Catalog(args.database).summary()
        elif args.command=='index':
            rows=read_json(args.documents)
            if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('资料应为对象数组')
            store=SearchStore(args.database);changed=[]
            for row in rows:
                if store.put(row['id'],row['revision'],row['fields']):changed.append(row['id'])
            result={'indexed':len(changed),'changed':changed,'verified_docs':store.verify()}
        elif args.command=='query':result=SearchStore(args.database).render(args.expression)
        elif args.command=='query-file':
            store=SearchStore(args.database);result=[]
            for row in read_json(args.queries):
                result.append({'id':row['id'],'query':row['text'],'hits':store.render(row['text'])})
        elif args.command=='delete':result={'deleted':SearchStore(args.database).delete(args.doc_id)}
        else:result={'verified_docs':SearchStore(args.database).verify()}
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except QueryError as exc:
        print('查询语法错误: %s'%exc,file=sys.stderr);return 2
    except (ValueError,OSError,KeyError) as exc:
        print('资料读取失败，请核对文件与字段约定: %s'%exc,file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
