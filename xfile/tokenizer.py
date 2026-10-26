import re


X_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]|[;,]|[^\s{};,"]+')


# Splits the text form into strings, braces, separators and bare words after stripping // and # comments.
def x_tokens(text):
    text = re.sub(r"//[^\n]*|#[^\n]*", "", text)
    return X_TOKEN.findall(text)


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
