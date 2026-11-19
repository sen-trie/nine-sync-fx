import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *
import glob, random, struct, time
import paths
from ninesync_fx.xfile.cache import parse_x_uncached
from ninesync_fx.xfile.model import XBlock, XError
from ninesync_fx.xfile.parser import x_parse_tree, x_skip_block
from ninesync_fx.xfile.tokenizer import x_decode, x_number, x_run_tokens, x_tokens

failed = []
checks = 0


def check(label, ok):
    global checks
    checks += 1
    if not ok:
        failed.append(label)


def reference_body(tokens, i, block):
    while i < len(tokens):
        token = tokens[i]
        if token == "}":
            return i + 1
        if token in (";", ","):
            i += 1
            continue
        if token == "{":
            j = i + 1
            ref = []
            while j < len(tokens) and tokens[j] != "}":
                if tokens[j] not in (";", ","):
                    ref.append(tokens[j])
                j += 1
            block.children.append(XBlock("__ref__", ref[0] if ref else ""))
            i = j + 1
            continue
        if token.startswith('"'):
            block.data.append(token[1:-1])
            i += 1
            continue
        number = x_number(token)
        if number is not None:
            block.data.append(number)
            i += 1
            continue
        kind = token
        i += 1
        name = ""
        if i < len(tokens) and tokens[i] != "{":
            name = tokens[i]
            i += 1
        if i >= len(tokens) or tokens[i] != "{":
            raise XError("expected '{' after %s" % kind)
        if kind == "template":
            i = x_skip_block(tokens, i)
            continue
        i += 1
        child = XBlock(kind, name)
        if i < len(tokens) and tokens[i].startswith("<") and tokens[i].endswith(">"):
            i += 1
        i = reference_body(tokens, i, child)
        block.children.append(child)
    return i


def reference_tree(text):
    root = XBlock("root", "")
    reference_body(x_tokens(text), 0, root)
    return root


def value_key(value):
    if isinstance(value, float):
        return ("f", struct.pack("<d", value))
    return (type(value).__name__, value)


def tree_key(block):
    return (block.kind, block.name, [value_key(v) for v in block.data], [tree_key(c) for c in block.children])


def outcome(parse, text):
    try:
        return ("ok", tree_key(parse(text)))
    except Exception as error:
        return ("error", type(error).__name__, str(error))


def same(label, text):
    check(label, outcome(x_parse_tree, text) == outcome(reference_tree, text))


cases = {
    "mesh": 'Mesh m { 3; 0.0;1.0;2.0;, -1.5;2e3;.5;, 1.;-0.0;+4;; 1; 3;0,1,2;; MeshMaterialList { 1; 1; 0;; { Mat } } }',
    "material": 'Material Mat { 1.0;0.5;0.25;1.0;; 5.0; 0;0;0;; 0;0;0;; TextureFilename { "tex.png"; } }',
    "numeric name": "Frame 1 { 2; }",
    "numeric name then separator": "Frame 1; { 2; }",
    "two numeric names": "Frame 1 2 { }",
    "numeric reference": "Mesh { 1; { 1.0 } { 2, 3 } { ; } { } }",
    "guid": "Mesh m { <ABC-123> 1; 2; }",
    "template": "template Mesh { <3D82AB44> DWORD n; 1 2 3 [n] } Mesh { 1; }",
    "number then string": 'Mesh { 1.0"a;b" 2"c" }',
    "number then braces": "Mesh{1}Frame{2.5}",
    "float spellings": "Mesh { nan; inf; -inf; 1_0; 1e; 1e+; 1.0abc; 0x10; 1.0.0; -; .; +.5e-3; }",
    "unicode digits": "Mesh { １; １.5; 1２; }",
    "unusual whitespace": "Mesh { 1;　2;\f3;\v4,\xa05 }",
    "comments": "Mesh { 1; // 2; 3;\n 4; # 5\n 6.0e1 }",
    "strings with escapes": 'Mesh { "a\\"b"; "1;2"; 3 }',
    "unbalanced": "Mesh { 1; 2;",
    "missing brace": "Mesh m 1;",
    "trailing number": "Mesh { } 1 2 3",
    "empty": "",
}
for label, text in cases.items():
    same(label, text)

pieces = [
    "Mesh", "Frame", "Material", "template", "name", "{", "}", ";", ",", ";;", '"s;t"', '"a\\"b"', "1", "-2.5", ".5",
    "1.", "1e5", "1e", "+3", "nan", "inf", "1_0", "１", "0x10", "1.0abc", "<ABC-123>", "//c\n", "#c\n", "-", ".",
    "3;0,1,2;", "0.0;1.0;2.0;,",
]
gaps = ["", " ", "\n", "\t", "　", "\f", "  "]
rng = random.Random(7)
fuzz_same = 0
for n in range(3000):
    text = "".join(rng.choice(pieces) + rng.choice(gaps) for _ in range(rng.randint(1, 40)))
    fuzz_same += outcome(x_parse_tree, text) == outcome(reference_tree, text)
check("random token soups parse the same", fuzz_same == 3000)
print("random token soups: %d of 3000 the same" % fuzz_same)

files = sorted(set(glob.glob(os.path.join(paths.HERE, "fixtures", "**", "*.x"), recursive=True)))
lv2 = os.environ.get("NINESYNC_THUNDER_LV2", os.path.join(paths.REPO, "debug-src"))
files += sorted(glob.glob(os.path.join(lv2, "*.x")))
fixtures = os.environ.get("NINESYNC_FIXTURES")
if fixtures:
    files += sorted(glob.glob(os.path.join(fixtures, "**", "*.x"), recursive=True))
for path in files:
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[8:12] != b"txt ":
        continue
    text = x_decode(raw[16:])
    started = time.time()
    new = outcome(x_parse_tree, text)
    middle = time.time()
    old = outcome(reference_tree, text)
    ended = time.time()
    check("%s parses the same" % os.path.basename(path), new == old)
    expanded = []
    for token in x_run_tokens(text):
        expanded.extend(token.tokens() if not isinstance(token, str) else [token])
    check("%s run tokens expand to the plain tokens" % os.path.basename(path), expanded == x_tokens(text))
    scene = parse_x_uncached(raw)
    print("%-24s %s, %d mesh(es), parse %.2fs (token by token %.2fs)" % (os.path.basename(path), "same" if new == old else "DIFFERENT", len(scene.meshes), middle - started, ended - middle))
check("some .x files were compared", len(files) > 0)
print(".x parser: %d of %d checks passed" % (checks - len(failed), checks))
print("FAILED:", failed or "none")
