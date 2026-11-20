import hashlib

from ..common.constants import (
    MME_ATTR_AFFINE,
    MME_VARYING_PREFIX,
)


# Readable group name with a short hash of the key; node group names are limited to 63 bytes, so the label is
# shortened first.
def mme_group_name(label, tag, key):
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:6]
    text = "MME %s [%s] %s" % (label, tag, digest)
    if len(text.encode("utf-8")) > 63:
        text = "MME %s [%s] %s" % (label[:30], tag, digest)
    return text


# Property or attribute name for a prefix/name/index; over-long names are replaced by a hash so they stay
# unique within Blender's name limit.
def mme_prop_name(prefix, name, index=None):
    text = prefix + name if index is None else "%s%s_%d" % (prefix, name, index)
    if len(text.encode("utf-8")) > 63:
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
        text = (prefix + digest) if index is None else "%s%s_%d" % (prefix, digest, index)
    return text


# Attribute name that carries one component of a vertex-shader output (varying) to the pixel-shader material.
def mme_varying_name(semantic, index):
    return "%s%s_%d" % (MME_VARYING_PREFIX, semantic, index)


# Instance attribute holding one block of the per-particle coefficients that rebuild a varying in the
# instanced material: 'k' is the constant, 'p', 'n' and 'uv' multiply position, normal and UV.
def mme_affine_name(varying, block):
    return "%s%s_%s" % (MME_ATTR_AFFINE, varying[len(MME_VARYING_PREFIX):], block)


MME_INPUT_NODE_PREFIX = "mmeIn_"
