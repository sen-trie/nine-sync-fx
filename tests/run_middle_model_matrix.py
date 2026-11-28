"""System-Python runner: each real PMX/FX pair gets a fresh Blender process."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess


def run(args):
    repo=Path(__file__).resolve().parents[1]
    rows=json.loads(args.catalog.read_text(encoding='utf-8'))['models']
    if args.families:rows=[row for row in rows if row['family'] in args.families]
    rows=[row for row in rows if row['status']=='CATALOGUED']
    presets=['\u9854','\u808c','\u9aea','\u670d','\u9aea2','\u305d\u306e\u4ed6']
    args.output.mkdir(parents=True,exist_ok=True)

    def execute(pair):
        index,row=pair;folder=args.output/f'{index:02}';folder.mkdir(exist_ok=True)
        entry=folder/'input.json';entry.write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
        fx=args.fx or args.toondx/(presets[index%len(presets)]+'.fx')
        command=[str(args.blender),'-b','--factory-startup','--python-exit-code','1','--python',str(repo/'tests/t_middle_model_matrix.py'),
                 '--','--entry',str(entry),'--fx',str(fx),'--mmd-tools',str(args.mmd_tools),'--output',str(folder)]
        env=dict(os.environ,PYTHONIOENCODING='utf-8',NINESYNC_FX=str(repo),BLENDER_USER_RESOURCES=str(folder/'profile'))
        try:
            done=subprocess.run(command,cwd=repo,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=args.timeout)
            (folder/'test.log').write_bytes(done.stdout)
            result=json.loads((folder/'results.json').read_text(encoding='utf-8')) if done.returncode==0 else dict(row,execution='FAILED')
            result.update(exit_code=done.returncode,log=str(folder/'test.log'),command=command)
        except subprocess.TimeoutExpired as error:
            (folder/'test.log').write_bytes(error.stdout or b'')
            result=dict(row,execution='TIMEOUT',log=str(folder/'test.log'),timeout_seconds=args.timeout,command=command)
        print(row['family'],result['execution'],flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:results=list(pool.map(execute,enumerate(rows)))
    (args.output/'model-matrix-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(row['execution']=='PASS' for row in results) else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--blender',type=Path,required=True)
    parser.add_argument('--mmd-tools',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--fx',type=Path)
    group.add_argument('--toondx',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--families',nargs='+')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--timeout',type=int,default=240)
    raise SystemExit(run(parser.parse_args()))
