import bpy
import logging
import os
import re
import sys


LOG_TEXT_NAME = "NineSyncLog"


LOG_TEXT_MAX_LINES = 3000


LOG_TEXT_KEEP_LINES = 2000


log = logging.getLogger("NineSync.FX")


HOME_TOKEN = "~"


def _profile_roots():
    roots = [os.path.expanduser("~")]
    for getter in (lambda: bpy.utils.resource_path("USER"), lambda: bpy.utils.extension_path_user(__package__.split(".")[0])):
        try:
            roots.append(getter())
        except Exception:
            pass
    return roots


def _root_pattern(root):
    if not root or root == "~":
        return None
    parts = [part for part in re.split(r"[\\/]+", root) if part]
    if not parts or (len(parts) == 1 and parts[0].endswith(":")):
        return None
    lead = r"[\\/]+" if root[0] in "\\/" else ""
    body = r"[\\/]+".join(re.escape(part) for part in parts)
    return r"(?<![\w.\-\\/])" + lead + body + r"(?![\w.\-])"


# Replaces the user's home folder (and Blender's profile folders when they lie elsewhere) with "~" in text that is saved
# inside the .blend. Matches either separator or a doubled one, ignores case on Windows, and only at a path boundary.
# When no home folder can be determined the text is returned unchanged. Duplicated in the core add-on on purpose.
def redact_home(text):
    try:
        flags = re.IGNORECASE if os.name == "nt" else 0
        for root in _profile_roots():
            pattern = _root_pattern(root)
            if pattern:
                text = re.sub(pattern, lambda _m: HOME_TOKEN, text, flags=flags)
    except Exception:
        pass
    return text


# Mirrors the log into the shared NineSyncLog text block so users can copy it from inside Blender; logging
# must never raise, hence the blanket except.
class TextBlockHandler(logging.Handler):
    def emit(self, record):
        try:
            text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(
                LOG_TEXT_NAME
            )
            if len(text.lines) > LOG_TEXT_MAX_LINES:
                keep = [line.body for line in text.lines][-LOG_TEXT_KEEP_LINES:]
                text.clear()
                text.write("\n".join(keep) + "\n")
            text.write(redact_home(self.format(record)) + "\n")
        except Exception:
            pass


# Rebuilds the handlers on every register so a reload does not stack duplicate handlers and double every line.
def setup_logging():
    for handler in list(log.handlers):
        log.removeHandler(handler)
    formatter = logging.Formatter(
        "[NineSync FX %(asctime)s %(levelname)s] %(message)s", "%H:%M:%S"
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    block = TextBlockHandler()
    block.setFormatter(formatter)
    log.addHandler(stream)
    log.addHandler(block)
    log.setLevel(logging.DEBUG)
    log.propagate = False
