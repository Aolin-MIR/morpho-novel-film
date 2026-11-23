"""Compose scoped art effects on an aligned plate, keeping the photographed world intact."""
import argparse
from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image

from film_art_routes import glitch_is_readable

FPS = 24
SIZE = (1536, 864)


def smooth(left, right, x):
    f = np.clip((x-left)/(right-left),0,1)
    return f*f*(3-2*f)


def load_plate(path):
    image = Image.open(path).convert('RGB')
    w,h = image.size
    ratio=SIZE[0]/SIZE[1]
    if w/h>ratio:
        middle=w/2
        image=image.crop((round(middle-h*ratio/2),0,round(middle+h*ratio/2),h))
    elif w/h<ratio:
        middle=h/2
        image=image.crop((0,round(middle-w/ratio/2),w,round(middle+w/ratio/2)))
    return np.asarray(image.resize(SIZE,Image.Resampling.LANCZOS),dtype=np.float32)


def portal(base, below, x, y, age, controls):
    c, trigger = controls['seam'],controls['contact_y']
    progress = smooth(0,2.25,age)
    width = (.008+.214*progress)*smooth(0,.42,age)
    growth=np.abs(y-trigger)*1.34
    tearing=smooth(growth,growth+.18,progress)
    fabric = .004*np.sin(19*y+progress*1.6)+.002*np.sin(77*y)
    left = c - (width*.18+fabric)*tearing
    right = c + (width+fabric)*tearing
    edge = np.minimum(x-left,right-x)
    shape = smooth(-.002,.003,edge) * tearing
    distance = np.abs(edge)
    shadow = .18*np.exp(-distance*53)*shape
    warm = np.array([229.,183.,127.],dtype=np.float32)
    soft = (.19*np.exp(-distance*330)+.022*np.exp(-distance*40)) * tearing
    fold = np.exp(-((x-left+.005)/.008)**2) * shape
    carried = below*(1-shadow[...,None])
    mixed = base*(1-shape[...,None])+carried*shape[...,None]
    mixed += soft[...,None]*(warm - mixed)*.38
    mixed -= fold[...,None]*13.5
    return mixed


def screen_effect(base, below, time, controls):
    left,top,right,bottom=controls['screen']
    w,h=SIZE
    x0,x1,y0,y1=int(left*w),int(right*w),int(top*h),int(bottom*h)
    view=base[y0:y1,x0:x1]
    rgb=view.copy()
    screen=smooth(0,.20,time)*(1-smooth(.68,1.15,time))
    if not screen>0:
        return
    content=np.asarray(Image.fromarray(below.astype(np.uint8)).resize((x1-x0,y1-y0),Image.Resampling.BICUBIC),
                       dtype=np.float32)
    rows=np.arange(y1-y0)
    pulse=(.42+.58*np.sin(rows*.23+time*32)**2)*(rows%3!=0)
    for channel, direction in enumerate((1,0,-1)):
        rgb[...,channel]=np.roll(content[...,channel],round(direction*(2+6*screen)),axis=1)
    scan=(pulse*.27+.52)[:,None,None]
    rgb=rgb*scan + 21
    local_x=np.linspace(0,1,x1-x0,dtype=np.float32)[None,:]
    local_y=np.linspace(0,1,y1-y0,dtype=np.float32)[:,None]
    mask=smooth(0,.085,np.minimum(local_x,1-local_x))*smooth(0,.09,np.minimum(local_y,1-local_y))
    view += (rgb-view)*(screen*.85*mask)[...,None]


def regional_order(plate, controls, progress, kind, previous):
    if progress<=0:
        return
    x0,y0,x1,y1=[round(v*n) for v,n in zip(controls['region'],(*SIZE,*SIZE),strict=True)]
    region=plate[y0:y1,x0:x1]
    if kind=='pixel-sort':
        clean=region.copy()
        luma=clean @ np.array([.213,.715,.072],dtype=np.float32)
        for row,intensity in zip(clean,luma,strict=True):
            a=np.flatnonzero((intensity>=controls['low'])&(intensity<=controls['high']))
            for group in np.split(a,np.where(np.diff(a)>1)[0]+1):
                if len(group)>1:
                    row[group]=row[group[np.argsort(intensity[group],kind='stable')]]
        region += (clean-region)*progress
    else:
        for column in range(x1-x0):
            index=min(len(previous)-1,round(column/max(1,x1-x0-1)*(len(previous)-1)))
            src=previous[index][y0:y1,x0+column]
            region[:,column] += (src-region[:,column])*progress


def render(front, behind, spec, output):
    if any(key in spec and not glitch_is_readable(spec[key].get('intent'))
           for key in ('glitch', 'pixel-sort')):
        raise ValueError('故障类效果须交代明确的风格、观众先看到的铺垫、触发、视觉规律和结果。')
    live = front.suffix.lower() == '.mp4'
    first = None if live else load_plate(front)
    alternate = load_plate(behind)
    x=np.linspace(0,1,SIZE[0],dtype=np.float32)[None,:]
    y=np.linspace(0,1,SIZE[1],dtype=np.float32)[:,None]
    frame_count=round(spec['seconds']*FPS)
    output.parent.mkdir(parents=True,exist_ok=True)
    pending=output.with_suffix('.part.mp4')
    encoder=['ffmpeg','-nostdin','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1536x864',
             '-framerate','24','-i','-']
    decoder = nullcontext()
    if live:
        encoder += ['-i',str(front),'-map','0:v:0','-map','1:a:0','-c:a','copy']
        decoder = subprocess.Popen(
            ['ffmpeg','-nostdin','-v','error','-threads','2','-i',str(front),'-an','-vf',
             'fps=24,scale=1536:864','-frames:v',str(frame_count),'-pix_fmt','rgb24','-threads','2','-f','rawvideo','-'],
            stdout=subprocess.PIPE)
    else:
        encoder += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-c:a','aac']
    encoder += ['-t',str(frame_count/FPS),'-c:v','libx264','-threads','2','-pix_fmt','yuv420p',
                '-preset','fast','-crf','17','-movflags','+faststart',str(pending)]
    frame_bytes = SIZE[0]*SIZE[1]*3
    history=[]
    try:
        with decoder as reader, subprocess.Popen(encoder,stdin=subprocess.PIPE) as process:
            for n in range(frame_count):
                time=n/FPS
                if live:
                    data = reader.stdout.read(frame_bytes)
                    if len(data) != frame_bytes:
                        raise ValueError('原片没有提供完整的合成窗口。')
                    plate = np.frombuffer(data,dtype=np.uint8).reshape(SIZE[1],SIZE[0],3).astype(np.float32)
                else:
                    plate = first.copy()
                if glitch:=spec.get('glitch'):
                    screen_effect(plate, alternate, time-glitch['onset'], glitch)
                if seam:=spec.get('portal'):
                    plate=portal(plate, np.roll(alternate, n//12, axis=1), x, y, time-seam['onset'],seam)
                original = plate.copy() if 'slit' in spec else None
                for kind in ('pixel-sort','slit'):
                    if control:=spec.get(kind):
                        regional_order(plate,control,smooth(control['onset'],control['peak'],time),
                                       kind,history or [plate.copy()])
                if original is not None:
                    history=(history+[original])[-9:]
                finished = np.clip(plate,0,255).astype(np.uint8)
                if not live:
                    shift=2.5*time
                    finished=np.asarray(Image.fromarray(finished).transform(
                        SIZE,Image.Transform.AFFINE,(.982,0,12+shift,0,.982,7),Image.Resampling.BICUBIC))
                process.stdin.write(finished.tobytes())
            process.stdin.close()
            if process.wait() or (live and reader.wait()):
                raise RuntimeError('艺术合成没有完成编码。')
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    evidence={'front_sha256':sha256(front.read_bytes()).hexdigest(),
              'behind_sha256':sha256(behind.read_bytes()).hexdigest(),
              'video_sha256':sha256(output.read_bytes()).hexdigest(), 'frames':frame_count,
              'techniques':{key:value for key,value in spec.items() if key!='seconds'},
              'review':'合成待实剪核对' if live else '二维构图与合成试制，人物表演另有实拍任务。'}
    output.with_suffix('.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('front',type=Path)
    parser.add_argument('behind',type=Path)
    parser.add_argument('spec',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    spec=json.loads(args.spec.read_text(encoding='utf-8'))
    if type(spec['seconds']) not in (int,float) or not 2<=spec['seconds']<=30:
        raise ValueError('需要有效的合成时长。')
    for key,value in spec.items():
        if key=='seconds':
            continue
        if key not in {'portal','glitch','pixel-sort','slit'} or not 0<=value['onset']<spec['seconds']:
            raise ValueError('效果必须拥有有效的触发时点。')
        if key=='portal' and not all(0<=value[field]<=1 for field in ('seam','contact_y')):
            raise ValueError('切口须贴在画面载体内。')
        if key in {'pixel-sort','slit'} and not value['onset']<value['peak']<=spec['seconds']:
            raise ValueError('效果的到达时刻须位于合成窗口内。')
        for field in ('screen','region'):
            if field in value:
                a,b,c,d=value[field]
                if not (0<=a<c<=1 and 0<=b<d<=1):
                    raise ValueError('效果超出指定承载区域。')
    render(args.front,args.behind,spec,args.output)


if __name__=='__main__':
    main()
