#!/usr/bin/env python3
"""市井资料库位置倒排索引：可运行示例。

演示：字符簇分词与原文字节定位、短语/布尔查询、NOT补集、修订替换与删除、
损坏编码检出。所有数据本地合成，运行结束自动清理临时数据库。

运行：python examples/search_demo.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from workbench.search import SearchStore
from workbench.query import QueryError

def banner(title):print('\n=== %s ==='%title)

def main():
    with tempfile.TemporaryDirectory() as tmp:
        store=SearchStore(Path(tmp)/'market.sqlite3')
        for row in json.loads((ROOT/'samples'/'documents.json').read_text(encoding='utf-8')):
            store.put(row['id'],row['revision'],row['fields'])

        banner('短语查询 "老店修鞋"（字段内相邻词位置，跨字段不拼接）')
        for hit in store.render('"老店修鞋"'):print(hit)

        banner('布尔与规范化：strasse AND café（ß折叠、全角、组合音标同词）')
        for hit in store.render('strasse AND café'):print(hit)

        banner('NOT 相对已索引全集求补，NOT命中允许无高亮')
        for hit in store.search('NOT 湖岸'):print(hit)

        banner('重复词各自定位：d3 的两处“修鞋”')
        print([h for h in store.render('"修鞋"') if h['doc_id']=='d3'][0])

        banner('语法错误抛异常，不返回空结果冒充成功')
        for bad in ('','"未闭合','a AND','()'):
            try:store.search(bad)
            except QueryError as exc:print('%-8s -> %s'%(repr(bad),exc))

        banner('同事务修订替换：d1 升到修订2，旧修订1被拒绝覆盖')
        store.put('d1',2,{'title':'新店','body':'只卖咖啡'})
        try:store.put('d1',1,{'title':'老店修鞋','body':''})
        except ValueError as exc:print('拒绝旧修订:',exc)
        print('替换后 "老店修鞋" 命中:',[h['doc_id'] for h in store.search('"老店修鞋"')])

        banner('删除 d3 后索引与NOT全集同步收缩')
        store.delete('d3')
        print('剩余文档:',store.all_doc_ids())
        print('NOT "修鞋" 全集:',[h['doc_id'] for h in store.search('NOT "修鞋"')])

        banner('编码完整性校验通过，文档数=%d'%store.verify())

if __name__=='__main__':
    main()
