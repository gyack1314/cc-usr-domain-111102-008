import zlib

# 差分变长整数编码（无符号LEB128，规范形式）
#
# 每个整数按7位小端分组编码：除最后一个字节外，每字节最高位为1；
# 最后一个字节最高位为0。规范形式要求使用最少字节，且末字节未用高位全为0，
# 整数限制在64位无符号范围内（最多10字节）。非规范、超长、截断或超界编码
# 均抛出CodecError，损坏数据不会被静默接受。
MAX_BYTES=10
MAX_VALUE=(1<<64)-1

class CodecError(ValueError):
    """倒排编码损坏或不合法。"""

def encode_uint(value):
    if type(value) is not int or value<0 or value>MAX_VALUE:
        raise CodecError('整数超出无符号64位范围')
    out=bytearray()
    while value>=0x80:
        out.append((value&0x7F)|0x80);value>>=7
    out.append(value)
    return bytes(out)

def decode_uint(buf,offset):
    result=0;shift=0
    for i in range(MAX_BYTES):
        if offset+i>=len(buf):raise CodecError('变长整数被截断')
        byte=buf[offset+i]
        if i==MAX_BYTES-1 and byte>0x01:raise CodecError('变长整数超出64位')
        result|=(byte&0x7F)<<shift
        if not byte&0x80:
            # 规范形式：多字节末字节不得为0（否则存在更短编码）
            if i>0 and byte==0:raise CodecError('变长整数非规范（含冗余零字节）')
            return result,offset+i+1
        shift+=7
    raise CodecError('变长整数过长')

def checksum(blob):
    return zlib.crc32(blob)&0xFFFFFFFF

# 词项倒排Blob布局（全部整数为上述uvarint，差分处显式注明）：
#
#   uvarint 文档数 N
#   重复 N 次：
#     uvarint 文档号差分（首篇为绝对doc_no，其后为 doc_no - 上一doc_no，必须>=1）
#     uvarint 字段数 F
#     重复 F 次：
#       uvarint 字段号（同一文档内严格递增，重复即损坏）
#       uvarint 命中数 K
#       重复 K 次：
#         uvarint 词位置（首命中为绝对position，允许为0；其后为 position - 上一position 的差分，必须>=1）
#         uvarint 原文起始字节 start
#         uvarint 原文字节长度 length（end=start+length，必须为正）
#
# 解码返回：[(doc_no, [(field_no, [(position,start,end), ...]), ...]), ...]
def encode_postings(postings):
    out=bytearray();prev_doc=None
    out+=encode_uint(len(postings))
    for doc_no,fields in postings:
        delta=doc_no if prev_doc is None else doc_no-prev_doc
        if delta<1:raise CodecError('文档号差分非正（重复文档号或未递增）')
        out+=encode_uint(delta);prev_doc=doc_no
        out+=encode_uint(len(fields));prev_field=-1
        for field_no,hits in fields:
            if field_no<=prev_field:raise CodecError('字段号未严格递增')
            prev_field=field_no
            out+=encode_uint(field_no);out+=encode_uint(len(hits))
            prev_pos=None
            for position,start,end in hits:
                if start<0 or end<=start:raise CodecError('原文字节范围非法')
                if prev_pos is None:
                    if position<0:raise CodecError('词位置为负')
                    out+=encode_uint(position)
                else:
                    pdelta=position-prev_pos
                    if pdelta<1:raise CodecError('词位置差分非正（重复位置）')
                    out+=encode_uint(pdelta)
                prev_pos=position
                out+=encode_uint(start);out+=encode_uint(end-start)
    return bytes(out)

def decode_postings(blob):
    offset=0
    def read():
        nonlocal offset
        value,offset=decode_uint(blob,offset);return value
    total=read();postings=[];prev_doc=None
    for _ in range(total):
        delta=read()
        if delta<1:raise CodecError('文档号差分非正（重复或损坏）')
        doc_no=delta if prev_doc is None else prev_doc+delta;prev_doc=doc_no
        field_count=read();fields=[];prev_field=-1
        for _f in range(field_count):
            field_no=read()
            if field_no<=prev_field:raise CodecError('字段号未严格递增')
            prev_field=field_no
            hit_count=read();hits=[];prev_pos=None
            for _h in range(hit_count):
                pdelta=read()
                if prev_pos is None:
                    position=pdelta  # 首命中为绝对位置
                else:
                    if pdelta<1:raise CodecError('词位置差分非正（重复位置或损坏）')
                    position=prev_pos+pdelta
                prev_pos=position
                start=read();length=read()
                if length<1:raise CodecError('原文字节长度非正')
                hits.append((position,start,start+length))
            fields.append((field_no,hits))
        postings.append((doc_no,fields))
    if offset!=len(blob):raise CodecError('倒排Blob存在未消费字节（尾部损坏）')
    return postings
