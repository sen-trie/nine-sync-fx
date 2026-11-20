import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_fx import *

failed = []
def check(label, ok):
    print("%-72s %s" % (label, "ok" if ok else "FAILED"))
    if not ok:
        failed.append(label)

logmod = importlib.import_module("ninesync_fx.common.log")
B = chr(92)
home = "C:" + B + "Users" + B + "ab"
saved = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
def set_home(value):
    for k in saved:
        os.environ[k] = value
set_home(home)
red = logmod.redact_home

check("backslash path", red("x " + home + B + "p" + B + "a.pmm") == "x ~" + B + "p" + B + "a.pmm")
check("forward slash and other case", red("c:" + "/users/AB/p/a.pmm") == "~/p/a.pmm")
check("doubled backslash (repr)", red("C:" + B * 2 + "Users" + B * 2 + "ab" + B * 2 + "p") == "~" + B * 2 + "p")
check("a longer sibling folder is untouched", red("C:" + B + "Users" + B + "abc" + B + "p") == "C:" + B + "Users" + B + "abc" + B + "p")
check("text without a home path is unchanged", red("nothing here") == "nothing here")
check("home alone at end of text", red("at " + home) == "at ~")
for empty in ("", "~"):
    set_home(empty)
    check("home %r leaves text unchanged" % empty, red(home + B + "p") == home + B + "p")
set_home(home)

text_name = logmod.LOG_TEXT_NAME
if bpy.data.texts.get(text_name):
    bpy.data.texts.remove(bpy.data.texts[text_name])
logmod.setup_logging()
mb.log.info("sync start: %s/x.pmm", home.replace(B, "/"))
try:
    raise ValueError(home + B + "boom")
except ValueError:
    mb.log.exception("failed")
body = bpy.data.texts[text_name].as_string()
check("the NineSyncLog text holds ~ and not the account name", "~/x.pmm" in body and "ab" + B not in body and "Users" not in body)
check("tracebacks are redacted too", "boom" in body and home not in body)

path = os.path.join(tempfile.mkdtemp(), "log.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
check("still redacted after save and reopen", "Users" not in bpy.data.texts[text_name].as_string() and "~/x.pmm" in bpy.data.texts[text_name].as_string())

text = bpy.data.texts[text_name]
text.clear(); text.write("\n".join("line %d" % i for i in range(logmod.LOG_TEXT_MAX_LINES + 5)) + "\n")
logmod.setup_logging()
mb.log.info("after trim")
lines = [l.body for l in bpy.data.texts[text_name].lines]
check("the preserving trim still keeps the last lines", len(lines) <= logmod.LOG_TEXT_KEEP_LINES + 3 and "line %d" % (logmod.LOG_TEXT_MAX_LINES + 4) in lines and "line 0" not in lines)

for k, v in saved.items():
    if v is None: os.environ.pop(k, None)
    else: os.environ[k] = v
print("FAILED: %s" % (", ".join(failed) if failed else "none"))
