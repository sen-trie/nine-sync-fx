import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness_fx import bpy, mb, setup_scene


FIX = Path(__file__).parent / "fixtures" / "mme" / "triangle"


def imported(folder):
    scene, collection, _, _ = setup_scene()
    scene.ninesync_fx.accessories.clear()
    result = bpy.ops.import_scene.ninesync_fx_accessory(filepath=str(folder / "triangle.x"), scale=1.0)
    assert result == {"FINISHED"}, result
    root = mb.find_accessory_root(collection, scene.ninesync_fx.accessories[-1].key)
    return root, json.loads(root.get(mb.MME_UNSOURCED_PROP, "null"))


root, unsourced = imported(FIX)
assert unsourced == [], unsourced

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    shutil.copytree(FIX, folder, dirs_exist_ok=True)
    shader = folder / "triangle.fx"
    source = shader.read_text().replace("Texture = <albedo>;", "Texture = <state>;").replace(
        "technique Object {", 'technique Object < string Script = "RenderColorTarget0=state;Pass=Step;'
        'RenderColorTarget0=;Pass=Surface;"; > {')
    source = source.replace("float4 ps(", "float4 step(Vertex input) : COLOR0 { return float4(1, 0, 0, 1); }\nfloat4 ps(")
    source = source.replace("    pass Surface {", '    pass Step < string Script = "Draw=Buffer;"; > {\n'
                            "        PixelShader = compile ps_3_0 step();\n    }\n    pass Surface {")
    shader.write_text("texture state : RENDERCOLORTARGET;\n" + source)
    root, unsourced = imported(folder)
    assert unsourced == ["Object.Surface: state"], unsourced
    assert json.loads(root[mb.MME_UNSUPPORTED_PROP]) == ["Object.Step"], root[mb.MME_UNSUPPORTED_PROP]
print("unsourced: plain triangle clean, buffer-fed surface reported")
