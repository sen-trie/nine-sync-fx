import re


FX_SCALARS = {
    "float": "f",
    "half": "f",
    "double": "f",
    "fixed": "f",
    "min16float": "f",
    "min10float": "f",
    "int": "i",
    "uint": "i",
    "dword": "i",
    "min16int": "i",
    "min12int": "i",
    "min16uint": "i",
    "bool": "b",
}


FX_TYPE_RE = re.compile(
    r"(float|half|double|fixed|min16float|min10float|int|uint|dword|min16int|min12int|min16uint|bool)([1-4])?(?:x([1-4]))?$"
)


FX_QUALIFIERS = {
    "static",
    "uniform",
    "const",
    "shared",
    "extern",
    "volatile",
    "inline",
    "row_major",
    "column_major",
    "precise",
    "groupshared",
    "nointerpolation",
    "linear",
    "centroid",
    "noperspective",
    "sample",
    "in",
    "out",
    "inout",
    "snorm",
    "unorm",
}


FX_SPECIAL_TYPES = (
    "texture",
    "texture1d",
    "texture2d",
    "texture3d",
    "texturecube",
    "sampler",
    "sampler1d",
    "sampler2d",
    "sampler3d",
    "samplercube",
    "sampler_state",
    "samplerstate",
    "string",
    "void",
    "vector",
    "matrix",
    "pixelshader",
    "vertexshader",
)


FX_BINARY_PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    ">": 7,
    "<=": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
}


FX_ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


FX_SWIZZLE = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}


FX_LOOP_LIMIT = 4096


FX_CALL_DEPTH = 48


FX_EVAL = {
    "num": "eval_num",
    "str": "eval_str",
    "id": "eval_id",
    "bin": "eval_bin",
    "un": "eval_un",
    "pre": "eval_incdec",
    "post": "eval_incdec",
    "assign": "eval_assign",
    "tern": "eval_tern",
    "cast": "eval_cast",
    "ctor": "eval_ctor",
    "member": "eval_member",
    "index": "eval_index",
    "call": "eval_call",
    "mcall": "eval_mcall",
    "comma": "eval_comma",
    "init": "eval_init_expr",
}


FX_NATIVE_INTRINSICS = {"cross", "normalize", "length", "dot", "distance", "reflect", "lerp", "smoothstep", "saturate", "clamp"}


FX_GROUP_INTRINSICS = {
    "mul",
    "smoothstep",
    "lerp",
    "normalize",
    "length",
    "distance",
    "cross",
    "dot",
    "reflect",
    "refract",
    "faceforward",
    "determinant",
}
