import hashlib
import numpy as np
from collections import OrderedDict

from ..common.constants import MME_ANALYSIS_DTYPE
from ..common.digest import file_digest
from .manifest import mme_texture_digest
from .variants import mme_exact_state_key


# Everything a strip probe state's vertex-shader outputs depend on apart from the state itself: the effect files, the
# textures the effect names, the pass, the vertex inputs the rows use, the loop instances and the batching. Two calls
# with equal keys and equal exact state keys evaluate the same shader on the same inputs.
def mme_sample_pass_key(runtime, draw, src, rows, instances, density, batch):
    h = hashlib.sha1()
    h.update(repr((
        np.dtype(MME_ANALYSIS_DTYPE).str,
        tuple(file_digest(name) for name in runtime.program.files),
        mme_texture_digest(runtime),
        draw.technique.name,
        draw.fx_pass.name,
        draw.subset,
        draw.order,
        repr(draw.loops),
        float(density),
        int(instances),
        int(batch),
    )).encode("utf-8"))
    for array in (np.asarray(src, dtype=np.int64), np.asarray(rows, dtype=np.int64), runtime.positions[src], runtime.normals[src], runtime.uvs[src]):
        array = np.ascontiguousarray(array)
        h.update(array.dtype.str.encode("ascii"))
        h.update(repr(array.shape).encode("ascii"))
        h.update(array.tobytes())
    return h.hexdigest()


# One loop instance's sampled outputs (float64 world positions, cull flags, varying components), stored once however
# many probe states or builds produced the same bits.
class MmeSampleBlob:
    __slots__ = ("world", "cull", "comps", "nbytes", "refs", "fingerprint")

    def __init__(self, world, cull, comps, fingerprint):
        self.world = world
        self.cull = cull
        self.comps = comps
        self.nbytes = world.nbytes + cull.nbytes + sum(c.nbytes for c in comps)
        self.refs = 0
        self.fingerprint = fingerprint

    def same(self, world, cull, comps):
        if not np.array_equal(self.cull, cull) or not mme_same_bits(self.world, world):
            return False
        return len(self.comps) == len(comps) and all(a.dtype == b.dtype and mme_same_bits(a, b) for a, b in zip(self.comps, comps))


def mme_same_bits(a, b):
    return a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a.view("u%d" % a.dtype.itemsize), b.view("u%d" % b.dtype.itemsize))


# Strip probe-state samples kept between builds of this session, so a rebuild that leaves some probe states as they
# were (a new opacity key changes only the keyed variants) replays their vertex-shader outputs instead of evaluating
# them again. An entry is found by the pass key and the exact state key over the globals the evaluation read, and
# replays the same arrays in the same batches, so the samples are the same bits. Instances are shared between entries
# by content, verified bit for bit, and the least recently used entries are dropped to stay within the byte budget.
class MmeSampleCache:
    def __init__(self):
        self.entries = OrderedDict()
        self.reads = {}
        self.blobs = {}
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def clear(self):
        self.entries.clear()
        self.reads.clear()
        self.blobs.clear()
        self.bytes = 0

    def find(self, pass_key, probe, program):
        for reads in self.reads.get(pass_key, ()):
            key = (pass_key, reads, mme_exact_state_key(probe, reads, program))
            found = self.entries.get(key)
            if found is not None:
                self.entries.move_to_end(key)
                self.hits += 1
                return reads, found
        self.misses += 1
        return None

    def batches(self, entry):
        for i0, i1, blobs in entry:
            world = np.concatenate([b.world for b in blobs])
            cull = np.concatenate([b.cull for b in blobs])
            comps = [np.concatenate([b.comps[k] for b in blobs]) for k in range(len(blobs[0].comps))] if blobs else []
            yield i0, i1, world, cull, comps

    def blob(self, prints, world, cull, comps):
        for found in self.blobs.get(prints, ()):
            if found.same(world, cull, comps):
                return found
        made = MmeSampleBlob(np.array(world), np.array(cull), [np.array(c) for c in comps], prints)
        self.blobs.setdefault(prints, []).append(made)
        return made

    # Adds one evaluated batch to an entry being collected; the arrays are copied only for instances not stored yet.
    def add(self, entry, i0, i1, world, cull, comps, per):
        n = i1 - i0
        count = n * per
        world = np.ascontiguousarray(np.asarray(world, dtype=np.float64).reshape(count, 3))
        cull = np.ascontiguousarray(np.broadcast_to(np.asarray(cull, dtype=bool), (count,)))
        comps = [np.ascontiguousarray(np.broadcast_to(np.asarray(c), (count,))) for c in comps]
        sums = [cull.reshape(n, per).sum(axis=1), world.view(np.uint64).reshape(n, -1).sum(axis=1, dtype=np.uint64)]
        sums.extend(c.view("u%d" % c.dtype.itemsize).reshape(n, per).sum(axis=1, dtype=np.uint64) for c in comps)
        prints = list(zip(*[x.tolist() for x in sums]))
        blobs = []
        for j in range(n):
            a, b = j * per, (j + 1) * per
            blob = self.blob(prints[j], world[a:b], cull[a:b], [c[a:b] for c in comps])
            if blob.refs == 0:
                self.bytes += blob.nbytes
            blob.refs += 1
            blobs.append(blob)
        entry.append((i0, i1, blobs))

    def commit(self, pass_key, reads, probe, program, entry, budget):
        key = (pass_key, reads, mme_exact_state_key(probe, reads, program))
        old = self.entries.pop(key, None)
        if old is not None:
            self.release(old)
        else:
            known = self.reads.setdefault(pass_key, {})
            known[reads] = known.get(reads, 0) + 1
        self.entries[key] = entry
        while self.bytes > budget and self.entries:
            (old_pass, old_reads, _state), old = self.entries.popitem(last=False)
            self.release(old)
            counts = self.reads.get(old_pass, {})
            counts[old_reads] -= 1
            if counts[old_reads] <= 0:
                del counts[old_reads]
            if not counts:
                self.reads.pop(old_pass, None)

    def release(self, entry):
        for _i0, _i1, blobs in entry:
            for blob in blobs:
                blob.refs -= 1
                if blob.refs == 0:
                    self.bytes -= blob.nbytes
                    found = self.blobs.get(blob.fingerprint, [])
                    if blob in found:
                        found.remove(blob)
                    if not found:
                        self.blobs.pop(blob.fingerprint, None)


mme_sample_cache = MmeSampleCache()
