"""Animate a subtitle word around the real tracked hand in a photographed take."""
import argparse
from bisect import bisect_right
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = (1536, 864)
FPS = 24


def fill(style, label):
    if label not in style or not isinstance(style[label], str) or not style[label]:
        raise ValueError(f'缺少越界字幕的{label}。')
    return style[label]


def positions(frames, t):
    at = [frame['at'] for frame in frames]
    index = bisect_right(at,t)
    if index == 0:
        return frames[0]['tip'], frames[0]['mask']
    if index == len(frames):
        return frames[-1]['tip'], frames[-1]['mask']
    a,b = frames[index-1:index+1]
    f = (t-a['at'])/(b['at']-a['at'])
    def move(left,right):
        return tuple(x+(y-x)*f for x,y in zip(left,right,strict=True))
    return move(a['tip'],b['tip']),[move(u,v) for u,v in zip(a['mask'],b['mask'],strict=True)]


def validate(spec, duration):
    line = fill(spec,'caption')
    word = fill(spec,'remove')
    if len(word)!=1 or line.count(word)!=1 or len(line)>14:
        raise ValueError('字幕须含一个可以唯一定位的否定词。')
    performance = spec['performance']
    for name in ('identity', 'gesture', 'scale', 'following_action'):
        fill(performance, name)
    for name in ('same_performer', 'arm_connected', 'perspective_consistent'):
        if performance.get(name) is not True:
            raise ValueError('尺幅越界应保留同一身体、动作链和投影比例。')
    frames = spec['tracking']
    begin,grab,release = (spec[key] for key in ('show','grab','release'))
    boundary = spec['boundary']
    if (not 0<=begin<grab<release<=duration
            or not .55<=boundary<=.85 or not .60<=spec['matte_opacity']<=1 or len(frames)<4):
        raise ValueError('必须标出真实跨界动作、接触和撤回。')
    last=-1
    vertex_count=len(frames[0]['mask'])
    for frame in frames:
        points=[frame['tip'],*frame['mask']]
        if (not last<frame['at']<=duration or not 4<=vertex_count<=28
                or len(frame['mask'])!=vertex_count
                or any(len(p)!=2 or not all(0<=axis<=1 for axis in p) for p in points)):
            raise ValueError('所有手部遮挡关键帧需使用等量的归一化顶点。')
        last=frame['at']
    touch=positions(frames,grab)[0]
    first=positions(frames,begin)[0]
    end=positions(frames,release)[0]
    if not first[1]<boundary<touch[1] or end[1]>=touch[1]-.06 or touch[1]>.93:
        raise ValueError('实拍须先跨过画幅，再捏住字位并带走。')
    return line,word,touch


def ease(t):
    t=max(0,min(1,t))
    return t*t*(3-2*t)


def assemble_frame(source,t,spec,letter,word,loc,font):
    show,grab,release=(spec[k] for k in ('show','grab','release'))
    if t<show:
        return source
    x0,y0=loc
    tip,polygon=positions(spec['tracking'],t)
    w,h=SIZE
    step=round(font.size*1.18)
    anchor=letter.index(word)
    x0,y0=x0*w,y0*h+font.size*.26
    gap=ease((t-grab-.24)/.50)
    boundary=round(h*spec['boundary'])
    frame=source.copy()
    draw=ImageDraw.Draw(frame)
    shade=Image.new('RGB',(w,h-boundary),(22,22,21))
    toned=Image.blend(source.crop((0,boundary,w,h)),shade,spec['matte_opacity'])
    frame.paste(toned,(0,boundary))
    draw.line((0,boundary,w,boundary),fill=(114,107,91),width=1)
    for index,char in enumerate(letter):
        if t>=grab and index==anchor:
            continue
        x=x0+(index-anchor)*step
        x+=(step*gap/2 if index<anchor else -step*gap/2) if index!=anchor else 0
        draw.text((x,y0),char,font=font,fill=(232,221,198),anchor='mm')
    if grab<=t<=release:
        tx,ty=tip[0]*w,tip[1]*h+font.size*.26
        size=round(font.size*(1-.82*ease((t-release+.30)/.30)))
        grip_font=font.font_variant(size=size) if size<font.size else font
        draw.text((tx+2,ty+3),word,font=grip_font,fill=(30,16,17),anchor='mm')
        draw.text((tx,ty),word,font=grip_font,fill=(193,99,77),anchor='mm')
    if t<=release:
        mask=Image.new('L',SIZE)
        ImageDraw.Draw(mask).polygon([(round(x*w),round(y*h)) for x,y in polygon],fill=255)
        frame.paste(source,(0,0),mask.filter(ImageFilter.GaussianBlur(1.35)))
    return frame


def render(src,spec,font_path,out):
    if src.resolve()==out.resolve():
        raise ValueError('越界合成需要独立输出文件。')
    duration=float(subprocess.check_output(
        ['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(src)],
        text=True,timeout=20))
    line,word,loc=validate(spec,duration)
    digest=sha256(src.read_bytes()).hexdigest()
    if spec['sha256']!=digest:
        raise ValueError('手势的追踪记录对应另一版原片。')
    font=ImageFont.truetype(str(font_path),int(spec['font_size']))
    nw,nh=SIZE
    step=round(font.size*1.18)
    index=line.index(word)
    if (not font.size <= loc[0]*nw-index*step
            or loc[0]*nw+(len(line)-index-1)*step > nw-font.size
            or loc[1]*nh+font.size*1.2 > nh):
        raise ValueError('取字时字幕应完整进入画幅。')
    dimensions=f'{nw}x{nh}'
    decode=['ffmpeg','-nostdin','-v','error','-xerror','-i',str(src),'-an','-vf',
            f'scale={nw}:{nh},fps={FPS}','-threads','2','-f','rawvideo','-pix_fmt','rgb24','-']
    out.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(dir=out.parent,suffix='.mp4',delete=False) as handle:
        staged=Path(handle.name)
    encode=['ffmpeg','-nostdin','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',dimensions,
            '-framerate',str(FPS),'-i','-','-i',str(src),'-map','0:v:0','-map','1:a:0','-c:a','copy',
            '-c:v','libx264','-pix_fmt','yuv420p','-preset','fast','-crf','17','-threads','3','-movflags','+faststart',str(staged)]
    count=0
    try:
        with subprocess.Popen(decode,stdout=subprocess.PIPE) as read, subprocess.Popen(encode,stdin=subprocess.PIPE) as write:
            while raw:=read.stdout.read(nw*nh*3):
                if len(raw)!=nw*nh*3:
                    raise ValueError('原片视频解码过早结束。')
                source=Image.frombytes('RGB',SIZE,raw)
                picture=assemble_frame(source,count/FPS,spec,line,word,loc,font)
                write.stdin.write(picture.tobytes())
                count+=1
            write.stdin.close()
            if read.wait() or write.wait() or count < round(spec['release']*FPS):
                raise RuntimeError('越界合成没有保存完整的演出。')
        staged.replace(out)
    finally:
        staged.unlink(missing_ok=True)
    result={'source_sha256':digest,'output_sha256':sha256(out.read_bytes()).hexdigest(),
            'frames':count,'caption_before':line,'caption_after':line.replace(word,''),
            'contact_seconds':spec['grab'],'from_confirmed_tracking':True,
            'performance':spec['performance']}
    out.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','spec','font','output'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    render(args.source,json.loads(args.spec.read_text(encoding='utf-8')),args.font,args.output)


if __name__=='__main__':
    main()
