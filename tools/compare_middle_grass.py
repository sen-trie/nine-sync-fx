import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def erode(mask, iterations=2):
    for _ in range(iterations):
        padded=np.pad(mask,1)
        mask=padded[1:-1,1:-1]&padded[:-2,1:-1]&padded[2:,1:-1]&padded[1:-1,:-2]&padded[1:-1,2:]
    return mask


def metrics(native, raster, mask_native, mask_raster):
    error=np.abs(native-raster)
    union=mask_native|mask_raster;intersection=mask_native&mask_raster
    interior=erode(intersection)
    return dict(native_pixels=int(mask_native.sum()),raster_pixels=int(mask_raster.sum()),
        silhouette_iou=float(intersection.sum()/max(1,union.sum())),
        mean_error=float(error.mean()),foreground_mean_error=float(error[union].mean()) if union.any() else 0.,
        interior_mean_error=float(error[interior].mean()) if interior.any() else None,
        maximum_error=float(error.max()),interior_pixels=int(interior.sum()))


def packed(path):
    raw=np.asarray(Image.open(path).convert('RGB'),dtype=float)
    return (raw[...,0]*256+raw[...,1])/65535*2-1


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    report=dict(raster=str(args.raster.resolve()),native=str(args.native.resolve()),
                mask='RGB max > 1 byte; foreground union; interior intersection eroded twice in four directions',
                differences='absolute RGB error at 1x byte scale; numeric targets compared before display quantization',frames=[],targets=[])
    for update,frame in enumerate(args.frames):
        repeat='-repeat' if frame in args.frames[:update] else ''
        native=args.native/args.native_pattern.format(frame=frame,repeat=repeat)
        if not native.exists():native=args.native/args.native_pattern.format(frame=frame,repeat='')
        per_update=args.raster/f'update-{update:02d}-frame-{frame}'/'main.npy'
        raster=per_update if per_update.exists() else args.raster/f'main-{frame}.npy'
        a=np.asarray(Image.open(native).convert('RGB'),dtype=float)
        b=np.floor(np.clip(np.load(raster)[...,:3],0,1)*255+.5)
        if a.shape!=b.shape:raise ValueError(f'Unmatched resolution: {a.shape} / {b.shape}')
        row=metrics(a,b,a.max(2)>1,b.max(2)>1)
        row.update(frame=frame,update=update,native_file=str(native),raster_file=str(raster))
        report['frames'].append(row)
        Image.fromarray(np.abs(a-b).astype('uint8')).save(args.output/f'difference-{update:02d}-frame-{frame}.png')
        Image.fromarray(b.astype('uint8')).save(args.output/f'raster-{update:02d}-frame-{frame}.png')
    for frame in dict.fromkeys(args.frames):
        for target,channel in [('HitRT','r'),('HeightTex1','r'),('NormalTex','r'),('NormalTex','b')]:
            native=args.native/f'native-packed-{target}-{channel}-{frame}.png'
            raster=args.raster/f'{target}-{frame}.npy'
            if not native.exists() or not raster.exists():continue
            a=packed(native);b=np.load(raster)[...,0 if channel=='r' else 2]
            mask_a=a>.01 if target=='HitRT' else np.ones(a.shape,dtype=bool)
            mask_b=b>.01 if target=='HitRT' else np.ones(b.shape,dtype=bool)
            row=metrics(a,b,mask_a,mask_b)
            error=np.abs(a-b)
            tolerance=1/65535+float(np.finfo(np.float32).eps)
            row.update(target=target,channel=channel,frame=frame,encoding='signed scalar [-1,1] packed in RG uint16',
                       readback_tolerance=tolerance,pixels_above_readback_tolerance=int((error>tolerance).sum()))
            report['targets'].append(row)
            np.save(args.output/f'difference-{target}-{channel}-{frame}.npy',a-b)
            Image.fromarray(np.minimum(error*255*8,255).astype('uint8')).save(args.output/f'difference-{target}-{channel}-{frame}-8x.png')
    for frame in dict.fromkeys(args.frames):
        depth=args.native/f'native-hit-depth-{frame}.png'
        numeric=args.raster/f'update-{args.frames.index(frame):02d}-frame-{frame}/offscreen-HitRT-depth-default.npy'
        hit=args.raster/f'HitRT-{frame}.npy'
        if depth.exists() and numeric.exists():
            raw=np.asarray(Image.open(depth).convert('RGB'),dtype=float)
            a=(raw[...,0]*65536+raw[...,1]*256+raw[...,2])/16777215
            b=np.load(numeric).astype(np.float16).astype(float)
            native_mask=packed(args.native/f'native-packed-HitRT-r-{frame}.png')>.01
            numeric_mask=np.load(hit)[...,0]>.01
            row=metrics(a,b,native_mask,numeric_mask)
            row.update(target='HitRT winning depth',frame=frame,encoding='VS clip z/w written to R16F color, then unsigned RGB uint24',
                       numeric_encoding='depth winner rounded to float16 for the diagnostic comparison',readback_tolerance=1/16777215)
            row['mean_error']=None
            row['maximum_error']=float(np.abs(a-b)[native_mask&numeric_mask].max())
            row['clear_note']='Native color clear and numeric depth clear encode different background values; foreground/interior metrics are the defined comparison.'
            report.setdefault('depths',[]).append(row)
            if frame==args.frames[0]:report['depth']=row
    first=args.native/'native-matched-0.png';repeat=args.native/'native-matched-0-repeat.png'
    if first.exists() and repeat.exists():
        a=np.asarray(Image.open(first),dtype=int);b=np.asarray(Image.open(repeat),dtype=int)
        report['native_repeat']=dict(exact=bool(np.array_equal(a,b)),maximum_byte_error=int(np.abs(a-b).max()))
    (args.output/'comparison.json').write_text(json.dumps(report,indent=2))
    for row in report['frames']:
        print(row['update'],row['frame'],'IoU',round(row['silhouette_iou'],6),'foreground',round(row['foreground_mean_error'],4),'interior',round(row['interior_mean_error'],4))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('native','raster','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--native-pattern',default='native-matched-{frame}{repeat}.png')
    parser.add_argument('--frames',nargs='+',type=int,default=[0,10,20,0])
    run(parser.parse_args())
