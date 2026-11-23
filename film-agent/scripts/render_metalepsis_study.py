"""A 2D demonstration of a hand pulling a word into the story's picture."""
import argparse
import json
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


WIDTH, HEIGHT, FPS, SECONDS = 1536, 864, 24, 13.5
APERTURE = (64, 43, 1472, 671)
CONTACT, DEPART = 6.70, 7.33


def ease(t, start, end):
    step = min(1, max(0, (t-start)/(end-start)))
    return step * step * (3-2*step)


def vignette():
    y, x = np.mgrid[-1:1:HEIGHT*1j, -1:1:WIDTH*1j]
    grain = np.random.default_rng(12).normal(0, .48, x.shape)
    tone = 1.6*(1-x*x) + 1.9*(1-y*y) + grain
    output = np.clip(np.array([19, 21, 21])[None, None] + tone[..., None], 0, 255)
    return Image.fromarray(output.astype(np.uint8), 'RGB').convert('RGBA')


def source_frame(plate, time):
    left, top, right, bottom = APERTURE
    motion = ease(time, 0, 4.0) if time < 4.2 else ease(time, 4.2, 10.2)
    if 4.2 <= time < 10.36:
        crop = (.205-.019*motion, .095, .843-.023*motion, .822)
    else:
        crop = (.011+.006*motion, .112+.01*motion, .99-.010*motion, .892-.011*motion)
    box = tuple(v*n for v,n in zip(crop, [plate.width,plate.height]*2, strict=True))
    image = plate.crop(box)
    return ImageOps.fit(image,(right-left,bottom-top),Image.Resampling.LANCZOS)


def compose_plate(args, spec, process):
    plate=Image.open(args.plate).convert('RGB')
    outcome_plate=Image.open(args.outcome_plate).convert('RGB')
    hand=Image.open(args.hand).convert('RGBA')
    if hand.getchannel('A').getextrema() != (0,255):
        raise ValueError('需要带透明背景的动作素材。')
    hand.thumbnail((338,359),Image.Resampling.LANCZOS)
    extension=64
    sleeve=np.asarray(hand)[:32]
    rows=(np.arange(extension) // 2 + 1)[::-1].clip(max=len(sleeve)-1)
    soft=sleeve[rows].copy()
    soft[:,:,:3]=(soft[:,:,:3]*np.linspace(.64,1,extension)[:,None,None]).astype(np.uint8)
    soft[:,:,3]=(soft[:,:,3]*np.linspace(0,1,extension)[:,None]**1.5).astype(np.uint8)
    sleeve_fade=Image.fromarray(soft,'RGBA')
    studio=vignette()
    font=ImageFont.truetype(str(args.font),58)
    small=ImageFont.truetype(str(args.font),23)
    letters=spec['caption']
    remove=spec['remove']
    if len(remove)!=1 or letters.count(remove)!=1 or not 3<=len(letters)<=14:
        raise ValueError('试制需要一句短字幕及一个明确、唯一的取字。')
    pick=letters.index(remove)
    step=71
    baseline=756
    start=WIDTH/2-(len(letters)-1)*step/2
    hold=start+step*pick
    for frame in range(round(FPS*SECONDS)):
        t=frame/FPS
        image=studio.copy()
        x,y,r,b=APERTURE
        image.paste(source_frame(outcome_plate if t>=10.36 else plate,t),(x,y))
        draw=ImageDraw.Draw(image)
        draw.line((x,b,r,b),fill=(109,101,80,255),width=1)
        tail='五 香 街'
        draw.text((x,b+45),tail,font=small,anchor='lm',fill=(109,106,93,255))
        arrive=ease(t,4.20,CONTACT)
        leave=ease(t,CONTACT,DEPART+1.56)
        reset=ease(t,DEPART+.30,DEPART+1.23)
        glyph_x=hold+44*leave + 1.6*math.sin(9*t)*leave*(1-leave)
        glyph_y=baseline-212*leave
        hands_on=4.20<=t<10.36
        if hands_on:
            hx=glyph_x - hand.width*.526 + 94*(1-arrive)
            hy=glyph_y - hand.height*.937 - 299*(1-arrive) - 28*ease(t,9.4,10.36)
            image.alpha_composite(sleeve_fade,(round(hx),round(hy)-extension))
            image.alpha_composite(hand,(round(hx),round(hy)))
            draw=ImageDraw.Draw(image)
        for i,letter in enumerate(letters):
            if i==pick and t>=CONTACT:
                continue
            char_x=start+i*step+(step/2 if i<pick else -step/2)*reset
            if i==pick:
                char_x=hold
            draw.text((char_x,baseline),letter,font=font,fill=(238,225,195,255),anchor='mm')
        if CONTACT<=t<10.36:
            glow=ease(t,CONTACT,CONTACT+.4)
            ink=(round(238-52*glow),round(225-139*glow),round(195-125*glow),255)
            draw.text((glyph_x+3,glyph_y+4),remove,font=font,fill=(19,13,13,180),anchor='mm')
            draw.text((glyph_x,glyph_y),remove,font=font,fill=ink,anchor='mm')
        process.stdin.write(image.convert('RGB').tobytes())


def write_sound(path):
    rate=48000
    t=np.arange(round(rate*SECONDS))/rate
    random=np.random.default_rng(1986)
    breeze=random.normal(0,1,t.size)
    breeze=np.convolve(breeze,np.hanning(71)/35,mode='same')
    dry=np.zeros_like(t)
    for instant, pitch, level in [(0.7,168,.044),(2.45,105,.035),(5.10,128,.032),
                                  (6.71,540,.11),(7.26,805,.039),(10.92,129,.11),
                                  (11.22,119,.085)]:
        elapsed=np.maximum(t-instant,0)
        transient=(t>=instant)*(1-np.exp(-elapsed*700))*np.exp(-elapsed*22)
        dry+=level*transient*(.49*np.sin(2*np.pi*pitch*elapsed)+.19*np.sin(2*np.pi*pitch*2.38*elapsed)
                              +.28*random.normal(0,1,t.size))
    pull=np.exp(-((t-7.03)/.28)**2)*np.diff(np.r_[0,breeze])*.43
    quiet=.10+.90*np.clip(np.abs(t-6.69)/.57,0,1)
    bed=quiet*(breeze*.022 + np.sin(2*np.pi*46*t)*.008*(.62+.38*np.sin(t*.58)**2))
    fade=np.clip((SECONDS-t)*4,0,1)
    with wave.open(str(path),'wb') as handle:
        handle.setparams((2,2,rate,0,'NONE','not compressed'))
        stereo=np.column_stack(((bed+dry+pull)*fade,(np.roll(bed,370)+np.roll(dry,63)*.86-pull*.65)*fade))
        handle.writeframes((np.clip(stereo,-1,1)*32767).astype('<i2').tobytes())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for path in ('plate','outcome_plate','hand','font','voice','spec','output'):
        parser.add_argument(path,type=Path)
    args=parser.parse_args()
    spec=json.loads(args.spec.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as d:
        foley=Path(d)/'foley.wav'
        temporary=Path(d)/'study.mp4'
        write_sound(foley)
        encoder = [
            'ffmpeg','-nostdin','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
            '-s',f'{WIDTH}x{HEIGHT}','-r',str(FPS),'-i','-','-i',str(foley),'-i',str(args.voice),
            '-filter_complex','[2:a]volume=0.65,adelay=1100:all=1[voice];'
            '[1:a][voice]amix=inputs=2:normalize=0,alimiter=limit=0.85[mix]',
            '-map','0:v:0','-map','[mix]','-t',str(SECONDS),'-pix_fmt','yuv420p','-c:v','libx264',
            '-crf','17','-preset','fast','-threads','4','-c:a','aac','-b:a','192k',
            '-movflags','+faststart',str(temporary)]
        with subprocess.Popen(encoder,stdin=subprocess.PIPE) as process:
            compose_plate(args,spec,process)
            process.stdin.close()
            if process.wait():
                raise RuntimeError('试片编码失败。')
        temporary.replace(args.output)


if __name__=='__main__':
    main()
