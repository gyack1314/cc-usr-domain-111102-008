# 市井资料的Unicode位置倒排与原文定位资料库

UTF-8原文、修订文档库与字符簇来源映射工具，以及带词位置的倒排索引与短语/布尔查询。 资料在samples，数据语义见docs/domain.md，本地存档说明见docs/catalog.md，索引编码与查询语义见docs/search.md。

## 运行

需要Python 3.11以上，仅使用标准库。

```bash
python -m workbench.cli inspect samples
python -m workbench.cli ingest samples state/raw.sqlite3
python -m workbench.cli catalog state/raw.sqlite3
python -m workbench.cli index samples/documents.json state/idx.sqlite3
python -m workbench.cli query state/idx.sqlite3 '"老店修鞋"'
python -m workbench.cli query-file state/idx.sqlite3 samples/queries.json
python -m workbench.cli verify state/idx.sqlite3
python examples/search_demo.py
python -m unittest discover -s tests -v
python -m compileall -q workbench tests examples
```

inspect查看文件行数和摘要；ingest将原始包放入SQLite；catalog列出原始包。index在与文档库同一事务内建立带位置倒排，query/query-file执行短语与AND/OR/NOT查询并把命中指回原文字节，verify校验差分编码完整性，delete删除文档及其索引。测试覆盖已有资料读取、存档幂等、领域原语与索引查询，compileall检查源码编译。业务计算不会由存档命令自动执行。

## 容器

```bash
docker build -t local-workbench .
docker run --rm --network none local-workbench
docker run --rm --network none local-workbench python -m unittest discover -s tests -v
```

程序不需要外部账号或常驻服务，容器默认查看合成资料。state与output为本地工作目录，不进入版本控制。workbench/io.py负责JSON与文件边界，catalog.py负责原始资料事务，domain.py只提供字符簇与文档库原语，tokens/codec/query/search.py在其上实现位置倒排与查询。
