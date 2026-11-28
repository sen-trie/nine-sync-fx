"""Read-only catalog of ordinary-costume PMX candidates for broader validation."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_middle_pmx import load_importer

# Choose distinct character/creator families before adding costume/rig variants.
FAMILIES = {
    "Miy__Cirno": "Ver1.1",
    "Cirno_V11b": "Cirno_V11b_STD.pmx",
    "miy-remilia": "Ver1.0",
    "Suke_Remilia": "without physic wing",
    "Miy Flandre": "Ver1.0",
    "Suke__Flandre": "Big Wings No physics",
    "Miy-style__Daiyousei": "Daiyousei.pmx",
    "Miy-Suwako": "Ver1.0.pmx",
    "Miy_Kanako": "\u516b\u5742\u795e\u5948\u5b50.pmx",
    "miy_reimu": "Ver1.0.pmx",
    "shwarz reimu": "v2.pmx",
    "schwarz_marisa": "R.pmx",
    "Miy__Alice Margatroid_Ver 2": "Ver 2.1.pmx",
    "renngesabu__Alice": "ver1.4.pmx",
    "miy_koishi": "Ver1.0.pmx",
    "miy_satori": "Ver1.0.pmx",
    "miy_tewi": "\u56e0\u5e61\u3066\u3090.pmx",
    "Miy_orinver1.1": "ver1.1.pmx",
    "Miy_Reisen": "Ver1.1",
    "Miy-style__Rumia(Koittolly)": "Ver1.2.pmx",
    "miy_Hong Meiling": "Miy__Hong Meiling.pmx",
    "Nightbug": "Nightbug.pmx",
    "Matara": "\u6469\u591a\u7f85\u96a0\u5c90\u5948.pmx",
    "Keiki": "a00_",
    "SuikaTamo": "a00_",
    "Goma-bou D__Yukari": "\u8d64\u8272\u9053\u58eb\u670d).pmx",
    "Arlvit-Sakuya": "Type-S (Full_Jacket E_Long skirt_High heels)",
    "suguni_shanghai_doll": "\u4e0a\u6d77\u4eba\u5f62_ver1.00_\u4eba\u9593\u30b5\u30a4\u30ba.pmx",
    "\u96ea\u8431__Koakuma": "\u5c0f\u60aa\u9b54A.pmx",
}


def run(args):
    pmx,_ = load_importer(args.mmd_tools)
    paths = sorted(args.root.rglob("*.pmx"))
    rows=[]
    for family,needle in FAMILIES.items():
        matches=[p for p in paths if p.relative_to(args.root).parts[0]==family and needle in str(p)]
        matches=[p for p in matches if "r18" not in str(p).lower() and "r-18" not in str(p).lower()]
        if len(matches)!=1:
            rows.append(dict(family=family,status="SELECTION_NEEDS_REVIEW",matches=[str(p) for p in matches]))
            continue
        path=matches[0]
        try:
            model=pmx.load(str(path))
            missing=[]
            for texture in model.textures:
                target=Path(texture.path)
                target=target if target.is_absolute() else path.parent/target
                if not target.is_file():missing.append(str(target))
            row=dict(family=family,path=str(path),status="CATALOGUED",sha256=sha256(path.read_bytes()).hexdigest(),
                     name=model.name,vertices=len(model.vertices),faces=len(model.faces),materials=len(model.materials),
                     bones=len(model.bones),morphs=dict(Counter(type(m).__name__ for m in model.morphs)),
                     ik_bones=sum(b.isIK for b in model.bones),
                     grant_bones=sum(b.hasAdditionalRotate or b.hasAdditionalLocation for b in model.bones),
                     fixed_axis_bones=sum(b.axis is not None for b in model.bones),
                     local_axis_bones=sum(b.localCoordinate is not None for b in model.bones),missing_textures=missing)
            rows.append(row)
            print("CATALOGUED",family,row["vertices"],row["ik_bones"],row["grant_bones"],len(missing),flush=True)
        except Exception as error:
            rows.append(dict(family=family,path=str(path),status="READ_FAILED",error=str(error)))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"model-catalog.json").write_text(json.dumps(dict(root=str(args.root),total_pmx_files=len(paths),models=rows),ensure_ascii=False,indent=2),encoding="utf-8")
    print("CATALOG_SUMMARY",dict(Counter(r["status"] for r in rows)),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--mmd-tools",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--")+1:]))
