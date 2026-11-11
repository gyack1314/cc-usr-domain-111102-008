from .tokens import tokenize

class QueryError(ValueError):
    """查询语法错误；不允许返回空结果冒充成功。"""

# AST节点：
#   ('term', 词形)
#   ('phrase', [词形, ...])
#   ('and', 左, 右)  ('or', 左, 右)  ('not', 子式)

class _Lex:
    __slots__=('kind','value')
    def __init__(self,kind,value=None):self.kind=kind;self.value=value

def _lex(text):
    tokens=[];i=0;n=len(text)
    while i<n:
        char=text[i]
        if char.isspace():i+=1;continue
        if char=='(':tokens.append(_Lex('LPAREN'));i+=1;continue
        if char==')':tokens.append(_Lex('RPAREN'));i+=1;continue
        if char=='"':
            j=text.find('"',i+1)
            if j<0:raise QueryError('引号未闭合')
            inner=text[i+1:j]
            terms=[t.term for t in tokenize(inner)]
            if not terms:raise QueryError('空短语为语法错误')
            tokens.append(_Lex('PHRASE',terms));i=j+1;continue
        j=i
        while j<n and not text[j].isspace() and text[j] not in '()"':j+=1
        atom=text[i:j];i=j
        if atom in ('AND','OR','NOT'):tokens.append(_Lex(atom))
        else:
            terms=[t.term for t in tokenize(atom)]
            # 纯标点原子不产生词，等同于分隔符，直接跳过
            if terms:tokens.append(_Lex('WORD',terms))
    return tokens

class _Parser:
    def __init__(self,tokens):self.tokens=tokens;self.pos=0
    def peek(self):return self.tokens[self.pos] if self.pos<len(self.tokens) else None
    def take(self):
        token=self.peek()
        if token is None:raise QueryError('查询意外结束')
        self.pos+=1;return token
    def expect(self,kind):
        token=self.take()
        if token.kind!=kind:raise QueryError('需要 %s，实际为 %s'%(kind,token.kind))
    def parse(self):
        if not self.tokens:raise QueryError('空查询为语法错误')
        node=self.parse_or()
        if self.pos!=len(self.tokens):raise QueryError('查询尾部存在无法解析的记号')
        return node
    def parse_or(self):
        node=self.parse_and()
        while self.peek() is not None and self.peek().kind=='OR':
            self.take();node=('or',node,self.parse_and())
        return node
    def parse_and(self):
        # 同优先级左结合；相邻词、短语、括号或NOT式之间为隐式AND
        node=self.parse_unary()
        while True:
            token=self.peek()
            if token is None or token.kind in ('OR','RPAREN'):return node
            if token.kind=='AND':self.take()
            elif token.kind in ('WORD','PHRASE','NOT','LPAREN'):pass
            else:raise QueryError('AND层级出现意外记号: %s'%token.kind)
            node=('and',node,self.parse_unary())
    def parse_unary(self):
        token=self.peek()
        if token is None:raise QueryError('查询意外结束')
        if token.kind=='NOT':
            self.take();return ('not',self.parse_unary())
        return self.parse_primary()
    def parse_primary(self):
        token=self.take()
        if token.kind=='WORD':
            terms=token.value
            node=('term',terms[0])
            for term in terms[1:]:node=('and',node,('term',term))  # 多词原子按隐式AND
            return node
        if token.kind=='PHRASE':return ('phrase',token.value)
        if token.kind=='LPAREN':
            if self.peek() is not None and self.peek().kind=='RPAREN':raise QueryError('空括号为语法错误')
            node=self.parse_or();self.expect('RPAREN');return node
        raise QueryError('主表达式位置出现意外记号: %s'%token.kind)

def parse_query(text):
    if not isinstance(text,str):raise QueryError('查询必须是字符串')
    return _Parser(_lex(text)).parse()
