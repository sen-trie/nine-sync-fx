import os

from .lexer import fx_tokenize
from .params import fx_find_params
from .parser import FxParser
from .preprocessor import FxPreprocessor
from .program import FxProgram


FX_PARSE_CACHE_SIZE = 32


_fx_parse_cache = {}


# Size and mtime of the effect and every include; None if any file vanished.
def fx_files_stamp(files):
    stamp = []
    for name in files:
        try:
            st = os.stat(name)
        except OSError:
            return None
        stamp.append((name, st.st_size, st.st_mtime_ns))
    return tuple(stamp)


# Parsing big effects is slow and every rebuild asks for the same file, so results are cached (bounded) and
# reused while the stamps of the file and its includes are unchanged.
def fx_parse_file(path, defines=None):
    key = (os.path.abspath(path), tuple(sorted((defines or {}).items())))
    cached = _fx_parse_cache.get(key)
    if cached is not None and fx_files_stamp(cached[0].files) == cached[1]:
        return cached[0]
    program = fx_parse_file_uncached(path, defines)
    stamp = fx_files_stamp(program.files)
    if stamp is not None:
        if len(_fx_parse_cache) >= FX_PARSE_CACHE_SIZE:
            _fx_parse_cache.pop(next(iter(_fx_parse_cache)))
        _fx_parse_cache[key] = (program, stamp)
    return program


# Preprocess, tokenize, parse, then compute the user-visible parameters.
def fx_parse_file_uncached(path, defines=None):
    folder = os.path.dirname(os.path.abspath(path))
    pre = FxPreprocessor(folder, defines)
    text = pre.run_file(path)
    program = FxProgram()
    program.folder = folder
    program.files = list(dict.fromkeys(pre.files))
    FxParser(fx_tokenize(text), program).parse()
    program.params = fx_find_params(program)
    return program
