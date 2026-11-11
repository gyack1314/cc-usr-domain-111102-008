import json
import sqlite3
import unicodedata
from pathlib import Path
from .io import canonical

def clusters(text):
    spans=[];byte=0;current='';start=0
    for char in text:
        size=len(char.encode('utf-8'))
        if current and not unicodedata.combining(current[0]) and unicodedata.combining(char):current+=char
        else:
            if current:spans.append((current,start,byte))
            current=char;start=byte
        byte+=size
    if current:spans.append((current,start,byte))
    return spans
def normalized_chars(text):
    return [(char,start,end) for cluster,start,end in clusters(text) for char in unicodedata.normalize('NFKC',cluster).casefold()]

class DocumentStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:c.execute('CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body BLOB NOT NULL)')
    def put(self,id,revision,fields):
        if type(revision) is not int or revision<1 or not id or any(type(v) is not str for v in fields.values()):raise ValueError('文档无效')
        data=canonical(fields)
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT revision,body FROM documents WHERE id=?',(id,)).fetchone()
            if old and (old[0]>revision or (old[0]==revision and old[1]!=data)):raise ValueError('修订冲突')
            if old and old[0]==revision:return False
            c.execute('INSERT INTO documents VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body',(id,revision,data));return True
    def get(self,id):
        with sqlite3.connect(self.path) as c:row=c.execute('SELECT revision,body FROM documents WHERE id=?',(id,)).fetchone()
        if row is None:raise KeyError(id)
        return {'id':id,'revision':row[0],'fields':json.loads(row[1])}
