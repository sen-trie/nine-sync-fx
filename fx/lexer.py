import re


FX_TOKEN = re.compile(
    r"""
 (?P<ws>\s+)
|(?P<str>"(?:[^"\\]|\\.)*")
|(?P<num>0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFhHlLuU]*)
|(?P<id>[A-Za-z_]\w*)
|(?P<op><<=|>>=|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|==|!=|<=|>=|&&|\|\||<<|>>|::|[-+*/%<>=!&|^~?:;,.()\[\]{}#@\\$`'])
""",
    re.X | re.S,
)


# Returns (kind, text) pairs without whitespace and ends with an 'eof' token so the parser never indexes past
# the end.
def fx_tokenize(text):
    tokens = []
    for match in FX_TOKEN.finditer(text):
        kind = match.lastgroup
        if kind == "ws":
            continue
        tokens.append((kind, match.group(0)))
    tokens.append(("eof", ""))
    return tokens
