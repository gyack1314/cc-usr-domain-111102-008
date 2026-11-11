import unicodedata
from .domain import clusters

# 汉字区间：U+3400..4DBF、U+4E00..9FFF、U+20000..2FA1F，逐字成词
_HAN_RANGES=((0x3400,0x4DBF),(0x4E00,0x9FFF),(0x20000,0x2FA1F))

def is_han(char):
    code=ord(char)
    return any(lo<=code<=hi for lo,hi in _HAN_RANGES)

def _kind(char):
    # 0=ASCII字母数字 1=其余Unicode字母数字；非字母数字返回None
    if not char.isalnum():return None
    return 0 if ord(char)<128 else 1

class Token(tuple):
    """一词：(规范化词形, 字段内词位置, 原文UTF-8起始字节, 原文UTF-8结束字节)。"""
    __slots__=()
    def __new__(cls,term,position,start,end):
        return tuple.__new__(cls,(term,position,start,end))
    term=property(lambda self:self[0])
    position=property(lambda self:self[1])
    start=property(lambda self:self[2])
    end=property(lambda self:self[3])

def tokenize(text):
    """按字符簇约定分词。

    一个非组合字符及其后续组合字符为一簇，单独的前导组合字符各成一簇；
    每簇先NFKC再casefold，展开出的每个字符都映射到该簇原始UTF-8半开字节范围。
    规范化后汉字逐字成词，ASCII字母数字连续段成词，其余Unicode字母数字连续段
    成词，其他字符作分隔。位置在单个字段内从0连续编号，分隔符不占位置。
    """
    tokens=[];position=0;run=None
    def flush():
        nonlocal run,position
        if run is not None:
            tokens.append(Token(run[0],position,run[1],run[2]));position+=1;run=None
    for cluster,cstart,cend in clusters(text):
        for char in unicodedata.normalize('NFKC',cluster).casefold():
            if is_han(char):
                flush()  # 汉字打断任何连续段
                tokens.append(Token(char,position,cstart,cend));position+=1
                continue
            kind=_kind(char)
            if kind is None:
                flush()  # 分隔符：不产生词，也不占位置
                continue
            if run is None or run[3]!=kind:
                flush()
                run=[char,cstart,cend,kind]
            else:
                run[0]+=char
                if cend>run[2]:run[2]=cend  # 合并覆盖原簇，字节范围只扩不缩
    flush()
    return tokens
