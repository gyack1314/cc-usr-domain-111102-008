# 带位置倒排索引与原文定位

在`documents`表（与`workbench.domain.DocumentStore`同库同表、共享稳定ID与修订号）之上建立带词位置的倒排索引，支持短语与AND/OR/NOT布尔查询，命中字节范围指回未改写的UTF-8原文。

## 分词与位置

分词复用`workbench.domain.clusters`的字符簇约定：

- 一个非组合字符及其后续组合字符为一簇；单独的前导组合字符各成一簇（作分隔，不产词）。
- 每簇先做NFKC再`casefold`，展开出的每个字符都映射到该簇原始UTF-8半开字节范围`[start,end)`。因此全角`ＡＢＣ`、`ß→ss`、`ﬁ→fi`、组合音标的展开不会让高亮错位到下一字。
- 规范化后：汉字（U+3400..4DBF、4E00..9FFF、20000..2FA1F）逐字成词；ASCII字母数字最大连续段成词；其余Unicode字母数字最大连续段成词；其他字符作分隔，不占词位置。
- 词位置在每个字段内从0连续编号；字段间位置永不相接，跨字段短语不可能命中。
- 不允许先整篇规范化再按字符数反推偏移：所有偏移都在字符簇层面记录原始字节。

## 查询语言

接受词、带引号短语、括号和`AND`/`OR`/`NOT`。`NOT`优先于`AND`，`AND`优先于`OR`；同优先级左结合。相邻词、短语或括号之间为隐式AND；一个原子被分成多词时按隐式AND处理，引号内容才是短语。

- 短语按字段内相邻词位置匹配；标点不产生空位，故字段内被标点隔开但位置相邻的词仍可成短语，而位置不相邻则不命中。
- `NOT`相对本次已索引的有效文档全集求补；只有NOT产生的命中允许没有高亮，其他命中必有原文来源。
- 空查询、空短语、空括号、未闭合引号、悬空运算符等均抛`QueryError`，不返回空结果冒充成功。

## 存储与差分变长编码

`SearchStore`与文档写入在**同一事务**内替换该文档的全部索引；`delete`同事务删除文档与索引。修订冲突规则与`DocumentStore.put`一致，旧修订不得覆盖新文档，同修订同内容幂等。

- `index_terms(term, postings, crc32)`：每词一行，postings为差分变长整数Blob，crc32为Blob校验和。
- `index_fields(doc_no, doc_id, field_no, name, length)`：文档号与字段号登记，doc_no从1起编（0在差分编码中不可能出现，损坏出0即被拒）。

变长整数为无符号LEB128规范形式（最少字节、64位上限）。Blob布局（见`workbench/codec.py`）：

```
uvarint 文档数
每文档: uvarint 文档号差分(首篇为绝对号, 其后差分>=1)
        uvarint 字段数
        每字段: uvarint 字段号(同文档内严格递增)
                uvarint 命中数
                每命中: uvarint 词位置(首项绝对位置可0, 其后差分>=1)
                        uvarint 起始字节 start
                        uvarint 字节长度 length
```

重复位置、非规范/截断/超长整数、尾部多余字节、字段号或文档号倒退都会在编码或解码时抛`CodecError`；Blob被改写导致校验和不符抛`IndexError_`。`SearchStore.verify()`做全量校验并核对原文范围不越界。

## 查询路径与原文定位

词查询只读取`index_terms`/`index_fields`，不扫描`documents`原文（测试以SQL跟踪断言匹配过程不接触documents表）。仅在渲染命中片段时才按已命中的文档读取原文：字节范围裁出的片段必须严格UTF-8可解码，且完整覆盖至少一个原词簇，否则抛错而非编造来源。短语高亮只合并相接的词字节，分隔符字节不并入。

## 运行

```bash
python -m workbench.cli index samples/documents.json state/idx.sqlite3
python -m workbench.cli query state/idx.sqlite3 '"老店修鞋"'
python -m workbench.cli query-file state/idx.sqlite3 samples/queries.json
python -m workbench.cli delete state/idx.sqlite3 d3
python -m workbench.cli verify state/idx.sqlite3
python examples/search_demo.py
python -m unittest discover -s tests -v
```
