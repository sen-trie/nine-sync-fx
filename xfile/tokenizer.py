import re


X_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]|[;,]|[^\s{};,"]+')


# X_TOKEN with one more kind: a stretch of number characters and separators that ends on a token boundary (group 1),
# which is most of a mesh's text.
X_RUN_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]|([0-9.+\-][0-9.eE+\-;, \t\r\n]*(?=[\s{};,"]|$))|[;,]|[^\s{};,"]+')


# A run of number and separator tokens kept as its text and numbers: the parser takes the numbers in one step, and
# looks at the single tokens only in the rare places where the token-by-token parse would.
class XRun:
    __slots__ = ("text", "values")

    def __init__(self, text, values):
        self.text = text
        self.values = values

    def tokens(self):
        return X_TOKEN.findall(self.text)


def x_strip_comments(text):
    return re.sub(r"//[^\n]*|#[^\n]*", "", text)


# Splits the text form into strings, braces, separators and bare words after stripping // and # comments.
def x_tokens(text):
    return X_TOKEN.findall(x_strip_comments(text))


# x_tokens with every run of numbers and separators as one XRun; expanding the runs gives x_tokens. Within the run
# characters float() reads exactly the plain numbers, so a stretch where it fails on any part (a lone '-', '1e') is
# given as its single tokens instead.
def x_run_tokens(text):
    tokens = []
    add = tokens.append
    for match in X_RUN_TOKEN.finditer(x_strip_comments(text)):
        if not match.lastindex:
            add(match.group())
            continue
        run = match.group()
        try:
            values = list(map(float, run.replace(";", " ").replace(",", " ").split()))
        except ValueError:
            tokens.extend(X_TOKEN.findall(run))
            continue
        add(XRun(run, values))
    return tokens


# None means the token is a keyword or name, not a number; the parser uses that to tell data from block
# headers.
def x_number(token):
    try:
        return float(token)
    except ValueError:
        return None


# MMD accessories are mostly Shift-JIS, so anything that is not valid UTF-8 is decoded as cp932. NULs from
# padded files are dropped first.
def x_decode(raw):
    raw = raw.replace(b"\x00", b"")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")
