import contextlib
import json
import sqlite3
from pathlib import Path
from .codec import CodecError,checksum,decode_postings,encode_postings
from .io import canonical
from .query import QueryError,parse_query
from .tokens import tokenize

class IndexError_(ValueError):
    """索引状态不合法（含倒排编码损坏）。"""

_SCHEMA='''
CREATE TABLE IF NOT EXISTS index_terms(
  term TEXT PRIMARY KEY,
  postings BLOB NOT NULL,
  crc32 INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS index_fields(
  doc_no INTEGER NOT NULL,
  doc_id TEXT NOT NULL,
  field_no INTEGER NOT NULL,
  name TEXT NOT NULL,
  length INTEGER NOT NULL,
  PRIMARY KEY(doc_no,field_no)
);
'''

class SearchStore:
    """与DocumentStore共用documents表的带位置倒排索引库。

    写索引与写文档在同一事务内完成；修订冲突规则与DocumentStore.put一致，
    旧修订不得覆盖新文档。词查询只读index_terms，不扫描原文。
    """
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body BLOB NOT NULL)')
            conn.executescript(_SCHEMA)

    @contextlib.contextmanager
    def _connect(self):
        conn=sqlite3.connect(self.path,timeout=5)
        try:
            with conn:yield conn
        finally:conn.close()

    # ---------- 写路径：文档与索引同一事务 ----------
    def put(self,doc_id,revision,fields):
        if type(revision) is not int or revision<1 or not doc_id or any(type(v) is not str for v in fields.values()):
            raise ValueError('文档无效')
        data=canonical(fields)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT revision,body FROM documents WHERE id=?',(doc_id,)).fetchone()
            if old and (old[0]>revision or (old[0]==revision and old[1]!=data)):raise ValueError('修订冲突')
            if old and old[0]==revision:return False
            conn.execute('INSERT INTO documents VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body',(doc_id,revision,data))
            self._reindex(conn)
            return True

    def delete(self,doc_id):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM documents WHERE id=?',(doc_id,)).fetchone() is None:return False
            conn.execute('DELETE FROM documents WHERE id=?',(doc_id,))
            self._reindex(conn)
            return True

    def _reindex(self,conn):
        # 重新编号：被删/重写文档的doc_no由其余文档按doc_id排序回收，字段号同规则
        rows=conn.execute('SELECT id,body FROM documents ORDER BY id').fetchall()
        # doc_no从1起编：0在差分编码中不可能出现，损坏出0会被解码层直接拒绝
        doc_nos={};field_rows=[]
        for doc_no,(did,blob) in enumerate(rows,start=1):
            doc_nos[did]=doc_no
            for field_no,name in enumerate(sorted(json.loads(blob).keys())):
                field_rows.append((doc_no,did,field_no,name,len(json.loads(blob)[name].encode('utf-8'))))
        conn.execute('DELETE FROM index_fields')
        conn.executemany('INSERT INTO index_fields VALUES(?,?,?,?,?)',field_rows)
        # 重建全部词项倒排：词查询只走该表
        terms={}
        for did,blob in rows:
            doc_fields=json.loads(blob)
            for field_no,name in enumerate(sorted(doc_fields.keys())):
                for token in tokenize(doc_fields[name]):
                    terms.setdefault(token.term,{}).setdefault(did,{}).setdefault(field_no,[]).append(
                        (token.position,token.start,token.end))
        conn.execute('DELETE FROM index_terms')
        for term,per_doc in terms.items():
            postings=[(doc_nos[did],sorted(per_field.items()))
                      for did,per_field in per_doc.items()]
            postings.sort()
            blob=encode_postings(postings);conn.execute('INSERT INTO index_terms VALUES(?,?,?)',(term,blob,checksum(blob)))

    # ---------- 文档读取 ----------
    def get(self,doc_id):
        with self._connect() as conn:
            row=conn.execute('SELECT revision,body FROM documents WHERE id=?',(doc_id,)).fetchone()
        if row is None:raise KeyError(doc_id)
        return {'id':doc_id,'revision':row[0],'fields':json.loads(row[1])}

    def all_doc_ids(self):
        with self._connect() as conn:return [r[0] for r in conn.execute('SELECT id FROM documents ORDER BY id')]

    # ---------- 索引读取（只接触index_*表） ----------
    def _postings(self,conn,term):
        row=conn.execute('SELECT postings,crc32 FROM index_terms WHERE term=?',(term,)).fetchone()
        if row is None:return []
        blob,crc=row
        if checksum(blob)!=crc:raise IndexError_('词项 %s 校验和不符，倒排已损坏'%term)
        try:return decode_postings(blob)
        except CodecError as exc:raise IndexError_('词项 %s 编码损坏: %s'%(term,exc)) from exc

    def _doc_map(self,conn):
        return {r[1]:r[0] for r in conn.execute('SELECT DISTINCT doc_no,doc_id FROM index_fields')}

    def _field_meta(self,conn):
        # doc_id -> {field_no:(name,length)}
        meta={}
        for _,doc_id,field_no,name,length in conn.execute('SELECT doc_no,doc_id,field_no,name,length FROM index_fields'):
            meta.setdefault(doc_id,{})[field_no]=(name,length)
        return meta

    def verify(self):
        """全量校验：校验和、编码严格性、编号一致性与原文范围。返回文档数，损坏即抛错。"""
        with self._connect() as conn:
            id_to_no=self._doc_map(conn);no_to_id={no:did for did,no in id_to_no.items()}
            meta=self._field_meta(conn);seen=set()
            for term,blob,crc in conn.execute('SELECT term,postings,crc32 FROM index_terms'):
                if checksum(blob)!=crc:raise IndexError_('词项 %s 校验和不符'%term)
                try:postings=decode_postings(blob)
                except CodecError as exc:raise IndexError_('词项 %s 编码损坏: %s'%(term,exc)) from exc
                for doc_no,fields in postings:
                    seen.add(doc_no);doc_id=no_to_id.get(doc_no)
                    if doc_id is None:raise IndexError_('词项 %s 指向不存在的文档号 %s'%(term,doc_no))
                    for field_no,hits in fields:
                        if field_no not in meta[doc_id]:raise IndexError_('词项 %s 指向不存在字段'%term)
                        limit=meta[doc_id][field_no][1]
                        for _,start,end in hits:
                            if start<0 or end>limit:raise IndexError_('词项 %s 原文范围越界'%term)
            if seen-set(no_to_id):raise IndexError_('倒排引用了未登记文档')
            return len(no_to_id)

    # ---------- 查询 ----------
    def search(self,text):
        tree=parse_query(text)
        with self._connect() as conn:
            no_to_id={no:did for did,no in self._doc_map(conn).items()}
            meta=self._field_meta(conn)
            result=self._eval(conn,tree,set(no_to_id))
        hits=[]
        for doc_no in sorted(result):
            doc_id=no_to_id[doc_no];fields=result[doc_no]
            highlights=[]
            for field_no in sorted(fields):
                for start,end in sorted(fields[field_no]):
                    highlights.append({'field':meta[doc_id][field_no][0],'start':start,'end':end})
            hits.append({'doc_id':doc_id,'highlights':highlights})
        return hits

    def _eval(self,conn,node,all_docs):
        kind=node[0]
        if kind=='term':
            return self._term_hits(conn,node[1])
        if kind=='phrase':
            return self._phrase_hits(conn,node[1])
        if kind=='and':
            left=self._eval(conn,node[1],all_docs);right=self._eval(conn,node[2],all_docs)
            out={}
            for doc_no in left.keys() & right.keys():
                merged={}
                for field_no in left[doc_no].keys() | right[doc_no].keys():
                    spans=set(left[doc_no].get(field_no,()))|set(right[doc_no].get(field_no,()))
                    merged[field_no]=self._merge_spans(spans)
                out[doc_no]=merged
            return out
        if kind=='or':
            out={}
            for side in (node[1],node[2]):
                part=self._eval(conn,side,all_docs)
                for doc_no,fields in part.items():
                    target=out.setdefault(doc_no,{})
                    for field_no,spans in fields.items():
                        target.setdefault(field_no,set()).update(spans)
            return {doc_no:{f:self._merge_spans(s) for f,s in fields.items()} for doc_no,fields in out.items()}
        if kind=='not':
            positive=self._eval(conn,node[1],all_docs)
            return {doc_no:{} for doc_no in all_docs-set(positive)}
        raise QueryError('未知表达式类型: %s'%kind)

    @staticmethod
    def _merge_spans(spans):
        # 合并相交或相邻的字节区间，返回排序后的半开区间集合
        ordered=sorted(spans);merged=[]
        for start,end in ordered:
            if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
            else:merged.append((start,end))
        return merged

    def _term_hits(self,conn,term):
        out={}
        for doc_no,fields in self._postings(conn,term):
            out[doc_no]={field_no:self._merge_spans((s,e) for _,s,e in hits) for field_no,hits in fields}
        return out

    def _phrase_hits(self,conn,terms):
        per_term=[self._postings(conn,term) for term in terms]
        if any(not p for p in per_term):return {}
        maps=[{doc_no:dict(fields) for doc_no,fields in p} for p in per_term]
        out={}
        for doc_no in set(maps[0]).intersection(*(set(m) for m in maps[1:])):
            field_out={}
            for field_no,hits0 in maps[0][doc_no].items():
                if any(field_no not in m[doc_no] for m in maps[1:]):continue
                # 每词的 词位置->字节范围；短语要求位置严格相邻（pos0+k），
                # 标点不占位，因此被标点隔开的词位置仍连续，可成短语；
                # 但字节范围只合并相接的词簇，分隔符字节不并入高亮
                chains=[{pos:(s,e) for pos,s,e in m[doc_no][field_no]} for m in maps]
                matched=[]
                for pos0,span0 in chains[0].items():
                    spans=[span0];ok=True
                    for k in range(1,len(terms)):
                        span=chains[k].get(pos0+k)
                        if span is None:ok=False;break
                        spans.append(span)
                    if ok:matched.extend(spans)
                if matched:field_out[field_no]=self._merge_spans(matched)
            if field_out:out[doc_no]=field_out
        return out

    # ---------- 原文定位：只对已命中的文档读documents表 ----------
    def render(self,text):
        hits=self.search(text)
        with self._connect() as conn:
            bodies={r[0]:json.loads(r[1]) for r in conn.execute('SELECT id,body FROM documents')}
        rendered=[]
        for hit in hits:
            doc_id=hit['doc_id'];fields=bodies[doc_id];details=[]
            for h in hit['highlights']:
                raw=fields[h['field']].encode('utf-8');start,end=h['start'],h['end']
                if start<0 or end>len(raw) or start>=end:raise IndexError_('高亮范围越界: %s'%doc_id)
                snippet=raw[start:end].decode('utf-8')  # 严格UTF-8，簇边界保证可解码
                if not self._covers(fields[h['field']],start,end):raise IndexError_('高亮未覆盖原词: %s'%doc_id)
                details.append({'field':h['field'],'start':start,'end':end,'snippet':snippet})
            rendered.append({'doc_id':doc_id,'highlights':details})
        return rendered

    @staticmethod
    def _covers(field_text,start,end):
        # 范围必须完整覆盖至少一个词，且端点落在簇边界（UTF-8可解码且不劈裂字符簇）
        for token in tokenize(field_text):
            if token.start>=start and token.end<=end:return True
        return False
