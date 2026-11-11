# 领域数据约定

汉字区间为U+3400..4DBF、4E00..9FFF、20000..2FA1F，每字单独成词；ASCII字母数字最大连续串成词；其余Unicode isalnum字符最大连续串成词，遇汉字/ASCII/分隔符即结束。组合类以unicodedata.combining是否非零判断。原文编码UTF-8严格，不自动替换非法字符；字段名独立建立位置。词语查询若被分为多词，按隐式AND处理，引号内容才是短语；空查询及空短语为语法错误。字段匹配结果按doc_id、field、position排序。

全部资料为本地合成。samples保留来源状态，读取和存档不等于业务验证通过；异常记录不被自动修改。workbench/domain.py中的现有函数可独立调用，tests/domain_checks.py演示其行为。字符簇到原文字节的映射由workbench/tokens.py沿用上述约定实现，带位置倒排与AND/OR/NOT查询见docs/search.md。
