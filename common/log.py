import bpy
import logging
import sys


LOG_TEXT_NAME = "NineSyncLog"


LOG_TEXT_MAX_LINES = 3000


log = logging.getLogger("NineSync.FX")


# Mirrors the log into the shared NineSyncLog text block so users can copy it from inside Blender; logging
# must never raise, hence the blanket except.
class TextBlockHandler(logging.Handler):
    def emit(self, record):
        try:
            text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(
                LOG_TEXT_NAME
            )
            if len(text.lines) > LOG_TEXT_MAX_LINES:
                text.clear()
            text.write(self.format(record) + "\n")
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
