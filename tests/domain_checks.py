import tempfile
from pathlib import Path
from workbench.domain import clusters,normalized_chars,DocumentStore
def check(c,root,read):
    c.assertEqual(clusters('é'),[('é',0,3)])
    c.assertEqual(len(clusters('́̀')),2)
    values=normalized_chars('ß');c.assertEqual(values,[('s',0,2),('s',0,2)])
    with tempfile.TemporaryDirectory() as d:
        s=DocumentStore(Path(d)/'d.sqlite3');c.assertTrue(s.put('x',1,{'body':'甲'}))
        c.assertFalse(s.put('x',1,{'body':'甲'}));c.assertEqual(s.get('x')['fields']['body'],'甲')
        with c.assertRaises(ValueError):s.put('x',1,{'body':'乙'})
        s.put('x',2,{'body':'乙'})
        with c.assertRaises(ValueError):s.put('x',1,{'body':'甲'})
