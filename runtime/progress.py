import bpy
import os
import sys
import time

from ..common.constants import (
    MME_PROGRESS_REDRAW,
    MME_PROGRESS_WIDTH,
)
from ..common.controls import mme_effect_roots
from ..common.state import _mme_state


# Effect roots to process: all of them, or just the named one.
def mme_target_roots(scene, root_name):
    roots = mme_effect_roots(scene)
    if root_name:
        roots = [r for r in roots if r.name == root_name]
    return roots


# Text progress bar for the status line.
def mme_progress_bar(fraction, width=MME_PROGRESS_WIDTH):
    fraction = max(0.0, min(1.0, fraction))
    filled = int(fraction * width)
    head = ">" if filled < width else ""
    return "[%s%s%s] %3d%%" % ("=" * filled, head, " " * (width - filled - len(head)), int(round(fraction * 100)))


# Window manager and workspace to show progress in, taking the first window when there is no active one.
def mme_progress_targets():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None, None
    window = getattr(bpy.context, "window", None)
    if window is None and len(wm.windows):
        window = wm.windows[0]
    workspace = getattr(window, "workspace", None) if window is not None else None
    return wm, workspace


# Forces a redraw so the status text appears during long Python work. Stdout and stderr are pointed at the
# null device meanwhile, because the redraw timer prints to them.
def mme_quiet_redraw():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (AttributeError, ValueError, OSError):
        pass
    saved = []
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        for fd in (1, 2):
            saved.append((fd, os.dup(fd)))
            os.dup2(null, fd)
        os.close(null)
    except OSError:
        for fd, copy in saved:
            os.dup2(copy, fd)
            os.close(copy)
        saved = None
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except (RuntimeError, AttributeError):
        pass
    finally:
        for fd, copy in saved or ():
            os.dup2(copy, fd)
            os.close(copy)


# Starts the shared progress state; returns False if another operation already owns it, so nested operations
# do not fight over the status bar.
def mme_progress_begin(context, title, count, start=0, end=0):
    if _mme_state.get("progress") is not None:
        return False
    _mme_state["progress"] = {
        "title": title,
        "count": max(1, count),
        "index": 0,
        "label": "",
        "within": 0.0,
        "detail": "",
        "start": start,
        "end": end,
        "drawn": 0.0,
        "began": time.time(),
    }
    wm, _workspace = mme_progress_targets()
    try:
        wm.progress_begin(0, 100)
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    mme_progress_draw(force=True)
    return True


# Moves to the next pass.
def mme_progress_step(index, label):
    progress = _mme_state.get("progress")
    if progress is None:
        return
    progress["index"] = index
    progress["label"] = label
    progress["within"] = 0.0
    progress["detail"] = ""
    mme_progress_draw(force=True)


# Progress inside the current pass (0 to 1) with a short description.
def mme_progress_within(within, detail=""):
    progress = _mme_state.get("progress")
    if progress is None:
        return
    progress["within"] = max(0.0, min(1.0, within))
    progress["detail"] = detail
    mme_progress_draw()


# Progress for a bake, from the current frame.
def mme_progress_tick(frame, force=False):
    progress = _mme_state.get("progress")
    if progress is None or progress["end"] <= progress["start"]:
        return
    span = progress["end"] - progress["start"] + 1
    done = max(0, min(span, frame - progress["start"] + 1))
    progress["within"] = done / span
    progress["detail"] = "frame %d/%d" % (done, span)
    mme_progress_draw(force)


# Updates the status text and progress bar; redraws are rate-limited unless forced.
def mme_progress_draw(force=False):
    progress = _mme_state.get("progress")
    if progress is None:
        return
    fraction = (progress["index"] + progress["within"]) / progress["count"]
    parts = [progress["title"], mme_progress_bar(fraction)]
    if progress["count"] > 1 or progress["label"]:
        parts.append("pass %d/%d %s" % (min(progress["index"] + 1, progress["count"]), progress["count"], progress["label"]))
    if progress["detail"]:
        parts.append(progress["detail"])
    parts.append("%.0fs" % (time.time() - progress["began"]))
    wm, workspace = mme_progress_targets()
    try:
        wm.progress_update(fraction * 100.0)
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    now = time.time()
    if not force and now - progress["drawn"] < MME_PROGRESS_REDRAW:
        return
    progress["drawn"] = now
    try:
        workspace.status_text_set("  ".join(parts))
    except (AttributeError, RuntimeError, ReferenceError):
        return
    if not bpy.app.background:
        mme_quiet_redraw()


# Clears the status text and progress bar.
def mme_progress_end():
    progress = _mme_state.get("progress")
    _mme_state["progress"] = None
    if progress is None:
        return
    wm, workspace = mme_progress_targets()
    try:
        wm.progress_end()
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    try:
        workspace.status_text_set(None)
    except (AttributeError, RuntimeError, ReferenceError):
        pass
