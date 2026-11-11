import sqlite3
import tempfile
import unittest
from pathlib import Path
from workbench.codec import CodecError,decode_postings,decode_uint,encode_postings,encode_uint
from workbench.domain import DocumentStore
from workbench.query import QueryError,parse_query
from workbench.search import IndexError_,SearchStore
from workbench.tokens import is_han,tokenize

ROOT=Path(__file__).resolve().parents[1]
SAMPLES=ROOT/'samples'/'documents.json'

def fresh_store():
    tmp=tempfile.TemporaryDirectory()
    store=SearchStore(Path(tmp.name)/'idx.sqlite3')
    return store,tmp

def index_samples(store):
    import json
    for row in json.loads(SAMPLES.read_text(encoding='utf-8')):
        store.put(row['id'],row['revision'],row['fields'])

class TokenTests(unittest.TestCase):
    def test_fullwidth_and_casefold(self):
        # 全角ＡＢＣ规范化为连续ASCII段，字节范围覆盖原9字节
        self.assertEqual([t.term for t in tokenize('ＡＢＣ')],['abc'])
        tok=tokenize('ＡＢＣ')[0];self.assertEqual((tok.start,tok.end),(0,9))
        # Straße -> strasse，两个展开的s都映射回原簇字节0:7
        toks=tokenize('Straße');self.assertEqual([t.term for t in toks],['strasse'])
        self.assertEqual((toks[0].start,toks[0].end),(0,7))
        # ligature ﬁlm 展开为 film
        self.assertEqual([t.term for t in tokenize('ﬁlm')],['film'])

    def test_combining_marks(self):
        # 前导组合字符各成簇且作分隔；非组合+组合为一簇
        leading=chr(0x0301)+'abc'  # U+0301 前导组合符
        self.assertEqual([t.term for t in tokenize(leading)],['abc'])
        decomposed='caf'+'e'+chr(0x0301)  # e + U+0301，真正分解形式（6字节）
        self.assertEqual(decomposed.encode().hex(),'63616665cc81')
        toks=tokenize(decomposed)
        self.assertEqual([t.term for t in toks],['caf','é'])
        # 高亮范围覆盖基字与组合符两个字节，不错位到下一字
        self.assertEqual((toks[1].start,toks[1].end),(3,6))
        # 预组合形式同词命中，且原文字节范围仍指回分解原文
        with tempfile.TemporaryDirectory() as d:
            store=SearchStore(Path(d)/'c.sqlite3')
            store.put('c',1,{'body':decomposed})
            hit=store.render('café')[0]['highlights'][0]
            self.assertEqual((hit['start'],hit['end'],hit['snippet']),(0,6,decomposed))

    def test_han_per_character_and_astral(self):
        self.assertEqual([t.term for t in tokenize('老店修鞋')],['老','店','修','鞋'])
        self.assertTrue(is_han(chr(0x20003)))
        toks=tokenize(chr(0x20003)+'乙')
        self.assertEqual([t.term for t in toks],[chr(0x20003),'乙'])
        self.assertEqual((toks[0].start,toks[0].end),(0,4))  # 4字节UTF-8

    def test_separators_hold_no_position(self):
        toks=tokenize('修鞋；咖啡')
        self.assertEqual([t.position for t in toks],[0,1,2,3])  # 标点不占位
        self.assertEqual([(t.start,t.end) for t in toks],[(0,3),(3,6),(9,12),(12,15)])

    def test_field_positions_never_touch(self):
        with tempfile.TemporaryDirectory() as d:
            store=SearchStore(Path(d)/'f.sqlite3')
            store.put('d',1,{'a':'甲乙','b':'甲乙'})
            # 两字段内“乙”的词位置都为1，证明字段各自从0编号、互不相接
            import sqlite3
            with sqlite3.connect(store.path) as conn:
                postings=store._postings(conn,'乙')
            field_hits={field_no:hits for _doc,fields in postings for field_no,hits in fields}
            self.assertEqual([h[0] for h in field_hits[0]],[1])
            self.assertEqual([h[0] for h in field_hits[1]],[1])
            # 跨字段伪短语不命中（字段间位置永不相接）
            self.assertEqual(store.search('"乙甲"'),[])

class CodecTests(unittest.TestCase):
    def test_uint_roundtrip_and_limits(self):
        values=[0,1,127,128,16383,16384,2**32,2**64-1]
        blob=b''.join(encode_uint(v) for v in values);off=0;got=[]
        for _ in values:
            v,off=decode_uint(blob,off);got.append(v)
        self.assertEqual(got,values);self.assertEqual(off,len(blob))
        with self.assertRaises(CodecError):encode_uint(2**64)
        with self.assertRaises(CodecError):encode_uint(-1)

    def test_corrupt_uint(self):
        for bad in (b'\x80',b'\x80\x00',b'\xff'*9+b'\x7f'):
            with self.assertRaises(CodecError):decode_uint(bad,0)

    def test_postings_roundtrip_and_differential(self):
        postings=[(1,[(0,[(0,0,3),(2,6,9)])]),(3,[(1,[(5,2,7)])])]
        blob=encode_postings(postings)
        self.assertEqual(decode_postings(blob),postings)
        for bad in (blob[:-1],blob+b'\x00',b'\x00'+blob[1:]):
            with self.assertRaises(CodecError):decode_postings(bad)

    def test_duplicate_position_rejected(self):
        with self.assertRaises(CodecError):
            encode_postings([(1,[(0,[(0,0,3),(0,3,6)])])])

class QuerySyntaxTests(unittest.TestCase):
    def test_precedence_and_associativity(self):
        self.assertEqual(parse_query('a b c'),parse_query('a AND b AND c'))
        tree=parse_query('a OR b AND NOT c')
        self.assertEqual(tree[0],'or');self.assertEqual(tree[2][0],'and')
        self.assertEqual(tree[2][2],('not',('term','c')))
        # 同优先级左结合
        self.assertEqual(parse_query('a OR b OR c'),
                         ('or',('or',('term','a'),('term','b')),('term','c')))

    def test_syntax_errors_raise_not_empty(self):
        for bad in ('','   ','"修鞋" AND','AND x','(abc','abc)','""','()','NOT','a OR','a AND OR b','"unterminated'):
            with self.assertRaises(QueryError):parse_query(bad)

    def test_phrase_and_split_word(self):
        self.assertEqual(parse_query('café'),('and',('term','caf'),('term','é')))
        self.assertEqual(parse_query('"老店 修鞋"'),('phrase',['老','店','修','鞋']))

class SearchTests(unittest.TestCase):
    def setUp(self):self.store,self.tmp=fresh_store();index_samples(self.store)
    def tearDown(self):self.tmp.cleanup()

    def test_sample_queries(self):
        q1=[h['doc_id'] for h in self.store.search('"老店修鞋"')]
        self.assertEqual(q1,['d1','d3'])
        q2=[h['doc_id'] for h in self.store.search('strasse AND café')]
        self.assertEqual(q2,['d1'])
        # 全部三篇都含“修鞋”，NOT补集为空（合法空结果，非语法错误）
        self.assertEqual(self.store.search('NOT "修鞋"'),[])
        q4=[h['doc_id'] for h in self.store.search('(abc OR film) AND NOT 湖岸')]
        self.assertEqual(q4,['d1','d2'])

    def test_highlights_point_to_original_bytes(self):
        hit=self.store.render('strasse')[0]
        self.assertEqual(hit['highlights'][0]['snippet'],'Straße')  # 未改写原文
        span=hit['highlights'][0]
        raw='Straße Café Café ＡＢＣ，街角修鞋。'.encode('utf-8')
        self.assertEqual(raw[span['start']:span['end']].decode('utf-8'),'Straße')
        # 全角词高亮指回全角原文而非折叠后的abc
        abc=[h for h in self.store.render('abc') if h['doc_id']=='d1'][0]
        self.assertEqual(abc['highlights'][0]['snippet'],'ＡＢＣ')

    def test_phrase_adjacency_and_cross_field(self):
        # 标点不产生空位：字段内跨标点的相邻词位置仍成短语
        self.assertEqual([h['doc_id'] for h in self.store.search('"修鞋咖啡"')],['d2'])
        # 真正不相邻的位置不命中
        self.assertEqual(self.store.search('"strasse abc"'),[])
        # 跨字段伪短语：d2 title“老店” 与 body“修鞋” 不得拼接
        self.assertNotIn('d2',[h['doc_id'] for h in self.store.search('"店修鞋"')])

    def test_phrase_span_excludes_separator_bytes(self):
        d3=[h for h in self.store.render('"老店 修鞋"') if h['doc_id']=='d3'][0]
        snippets=[h['snippet'] for h in d3['highlights']]
        self.assertEqual(snippets,['老店','修鞋'])  # 空格字节不并入高亮

    def test_repeated_words_give_separate_positions(self):
        # d3 body: 老店 修鞋 修鞋 —— “修鞋”出现两次，位置不同且各自高亮
        hits=self.store.render('"修鞋"')
        d3=[h for h in hits if h['doc_id']=='d3'][0]
        self.assertEqual(len(d3['highlights']),2)
        self.assertEqual([h['snippet'] for h in d3['highlights']],['修鞋','修鞋'])
        self.assertNotEqual(d3['highlights'][0]['start'],d3['highlights'][1]['start'])

    def test_not_only_hits_have_no_highlights(self):
        hits=self.store.search('NOT 湖岸')  # d1,d2 不含湖岸
        self.assertEqual([h['doc_id'] for h in hits],['d1','d2'])
        self.assertTrue(all(h['highlights']==[] for h in hits))
        # 普通命中不允许无高亮
        self.assertTrue(all(h['highlights'] for h in self.store.search('修鞋')))

    def test_revision_rules_and_reindex(self):
        original=self.store.get('d1')['fields']
        self.assertFalse(self.store.put('d1',1,original))  # 幂等
        with self.assertRaises(ValueError):self.store.put('d1',1,{'title':'x'})
        with self.assertRaises(ValueError):self.store.put('d1',0,{'title':'x'})
        self.store.put('d1',2,{'title':'新店','body':'只卖咖啡'})
        with self.assertRaises(ValueError):self.store.put('d1',1,original)  # 旧修订不覆盖
        self.assertNotIn('d1',[h['doc_id'] for h in self.store.search('"老店修鞋"')])
        self.assertEqual([h['doc_id'] for h in self.store.render('只卖')],['d1'])

    def test_delete_removes_index_and_universe(self):
        self.assertTrue(self.store.delete('d3'))
        self.assertFalse(self.store.delete('d3'))
        with self.assertRaises(KeyError):self.store.get('d3')
        self.assertEqual([h['doc_id'] for h in self.store.search('"老店 修鞋"')],['d1'])
        # 删除后NOT全集收缩
        self.assertEqual([h['doc_id'] for h in self.store.search('NOT "修鞋"')],[])

    def test_matching_does_not_scan_documents(self):
        conn=sqlite3.connect(self.store.path);logged=[]
        conn.set_trace_callback(logged.append)
        idmap=self.store._doc_map(conn)
        logged.clear()
        self.store._eval(conn,parse_query('老店 AND 修鞋 OR NOT 鞋'),set(idmap.values()))
        self.assertFalse(any('documents' in q for q in logged))
        conn.close()

    def test_corrupt_index_is_detected(self):
        conn=sqlite3.connect(self.store.path)
        blob,crc=conn.execute('SELECT postings,crc32 FROM index_terms WHERE term=?',('老',)).fetchone()
        conn.execute('UPDATE index_terms SET postings=? WHERE term=?',(blob[:-1]+bytes([blob[-1]^1]),'老'))
        conn.commit();conn.close()
        with self.assertRaises(IndexError_):SearchStore(self.store.path).search('老')
        with self.assertRaises(IndexError_):SearchStore(self.store.path).verify()

    def test_verify_clean_store(self):
        self.assertEqual(self.store.verify(),3)

    def test_compatible_with_document_store(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'shared.sqlite3'
            DocumentStore(path).put('z',1,{'body':'甲乙'})
            shared=SearchStore(path)  # 同库同documents表
            self.assertEqual(shared.get('z')['fields'],{'body':'甲乙'})
            shared.put('w',1,{'body':'丙'})
            self.assertEqual(DocumentStore(path).get('w')['fields'],{'body':'丙'})
            self.assertEqual([h['doc_id'] for h in shared.search('甲')],['z'])

if __name__=='__main__':
    unittest.main()
