"""Render an optical insert inside the chipped prison jar without raster input assets."""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np


def smooth(lo, hi, value):
    t = np.clip((value-lo)/(hi-lo), 0, 1)
    return t*t*(3-2*t)


def normalize(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def rgb(values):
    return np.asarray(values, dtype=np.float32)


NOISE = np.random.default_rng(7).random((256, 256), dtype=np.float32)


def texture(x, y, frequency):
    x, y = x*frequency, y*frequency
    ax, ay = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    sx, sy = x-ax, y-ay
    sx, sy = sx*sx*(3-2*sx), sy*sy*(3-2*sy)
    ix, iy = ax & 255, ay & 255
    jx, jy = (ax+1) & 255, (ay+1) & 255
    top = NOISE[iy,ix]*(1-sx) + NOISE[iy,jx]*sx
    bottom = NOISE[jy,ix]*(1-sx) + NOISE[jy,jx]*sx
    return (top*(1-sy) + bottom*sy).astype(np.float32)


def frame(grid, time):
    screen_x, screen_y = grid
    drift = .022 * (time / 3.75 - .5)
    eye = rgb([.09 + drift, -1.15, .62])
    target = rgb([.02 + drift * .4, .028, -.045])
    forward = normalize(target - eye)
    right = normalize(np.cross(forward, rgb([0, 0, 1])))
    up = normalize(np.cross(right, forward))
    view = normalize(forward + screen_x[..., None]*right + screen_y[..., None]*up)
    vx, vy, vz = np.moveaxis(view, -1, 0)
    t_rim = (.072-eye[2])/vz
    x, y = eye[0] + t_rim*vx, eye[1] + t_rim*vy
    radius = np.hypot(x, y)
    theta = np.arctan2(y, x)
    age = max(0., time-1.14)
    detail = texture(x,y,84)-.5
    pores = texture(x+1,y,224)**4
    ridge = .52 + .0022*np.sin(23*theta) + .0015*np.cos(73*theta)
    chip = .043*np.exp(-((theta+1.09)/.080)**4)
    lip_spread = np.abs(radius-(ridge-chip))
    lip = 1-smooth(.022, .027, lip_spread)
    lip_roughness = .56+.39*detail-.38*pores
    lip_direction = .31+.27*np.clip(-np.cos(theta+.65), 0, 1)
    lip_shade = rgb([.157,.095,.058]) * (lip_roughness+lip_direction)[...,None]
    lip_shade += rgb([.14,.086,.043]) * np.exp(-((lip_spread-.012)/.005)**2)[...,None] * lip_direction[...,None]
    floor_t = (-.69-eye[2])/vz
    fx, fy = eye[0]+floor_t*vx, eye[1]+floor_t*vy
    floor_texture = .19+.37*texture(fx,fy,6)+.18*texture(fx,fy,39)+.13*texture(fx,fy,140)
    occlusion = .27+.73*smooth(.48,1.23,np.hypot(fx+.13,fy-.12))
    warm_patch = .83 + .30 * np.exp(-((fx+.45+fy*.18)/.28)**2)
    floor = rgb([.23,.164,.107])*(floor_texture*occlusion*warm_patch)[...,None]
    centre = rgb([0,0,-.36])
    scale = rgb([.64,.64,.49])
    local = (eye-centre)/scale
    direction = view/scale
    a = np.sum(direction*direction, axis=-1)
    b = np.sum(local*direction, axis=-1)
    c = np.sum(local*local)-1
    discriminant = b*b-a*c
    hit = (-b-np.sqrt(np.maximum(0,discriminant)))/a
    body_position = (eye+hit[...,None]*view-centre)/scale
    body_height = body_position[...,2]*scale[2]+centre[2]
    body = (discriminant>0) & (hit>0) & (-.69<body_height) & (body_height<.071)
    normal_body = normalize(body_position/scale+1e-8)
    key = np.maximum(0, np.sum(normal_body*normalize(rgb([-.56,-.38,.74])),axis=-1))
    turning = .92+.08*np.sin(229*body_height+2*texture(x,y,12))
    ceramic = .52+.42*texture(body_position[...,0],body_height,42)-.27*texture(body_position[...,1],body_height,204)**5
    shade = rgb([.090,.056,.035])*((.27+.73*key)*ceramic*turning)[...,None]
    gloss = np.maximum(0,np.sum(normal_body*normalize(rgb([-.34,-.55,.72])),axis=-1))**10
    shade += rgb([.091,.058,.030])*(gloss*(.29+.53*ceramic))[...,None]
    composite = np.where(body[...,None],shade,floor)

    t_water = -eye[2]/vz
    wx, wy = eye[0]+t_water*vx, eye[1]+t_water*vy
    distance = np.maximum(1e-3,np.hypot(wx+.06,wy-.055))
    front = distance-age*.43
    window = np.exp(-(front/.12)**2) * (1-np.exp(-age*14)) * np.exp(-age*.68)
    phase = 86*front
    gradient = .0019*window*(86*np.cos(phase)-2*front/(.12**2)*np.sin(phase))
    gradient += .013*np.cos(17*distance-age*12)*np.exp(-((front+.085)/.16)**2)*(1-np.exp(-age*7))
    nx = -gradient*(wx+.06)/distance + .005*np.sin(43*wy+time)
    ny = -gradient*(wy-.055)/distance + .004*np.cos(32*wx-1.3*time)
    normal = normalize(np.stack((nx,ny,np.ones_like(nx)),axis=-1))
    incident = np.clip(-np.sum(view*normal,axis=-1),.05,1)
    reflected = view + 2*incident[...,None]*normal
    rx, ry, rz = np.moveaxis(reflected,-1,0)
    sky = 2.0/np.maximum(.14, rz)
    sx, sy = wx+sky*rx, wy+sky*ry
    slit = (1-smooth(.24,.29,np.abs(sx+.12))) * (1-smooth(.98,1.14,np.abs(sy-5.0)))
    upright = smooth(.017,.034,np.abs(sx+.025))
    reflected_light = (.24+1.35*slit*upright)*(.91+.09*np.sin(6*sy))
    fresnel = .021+.979*(1-incident)**5
    refraction = np.sqrt(1-(1-(incident**2))/1.333**2)
    travel = .13/np.maximum(.28,refraction)
    bottom_x = wx + travel*(vx/1.333 - nx*.18)
    bottom_y = wy + travel*(vy/1.333 - ny*.18)
    sediment = .33+.29*texture(bottom_x,bottom_y,36)+.22*texture(bottom_x,bottom_y,238)
    caustic = np.maximum(0,np.cos(180*bottom_x+16*np.sin(72*bottom_y)+22*gradient))**5
    liquid = rgb([.046,.074,.081])*(sediment*np.exp(-2*travel))[...,None]
    liquid += rgb([.040,.035,.016])*(caustic*window)[...,None]
    liquid = liquid*(1-fresnel[...,None])+rgb([1.9,1.35,.77])*(reflected_light*fresnel)[...,None]
    water_edge = 1-smooth(.473,.479,np.hypot(wx,wy))
    top_opening = 1-smooth(.482,.494,radius)
    water = water_edge*top_opening
    composite = composite*(1-water[...,None])+liquid*water[...,None]
    composite = composite*(1-lip[...,None])+lip[...,None]*lip_shade
    grain = (texture(screen_x,screen_y,1871)-.5)*.004
    exposure = (1-.36*np.clip(screen_x**2+screen_y**2,0,1)) * .98
    display = np.maximum(0,composite*exposure[...,None]+grain[...,None])
    display = display/(display+.76)
    return np.clip(255*np.power(display,.46),0,255).astype(np.uint8)


def render(out, width, height):
    fps, count = 24, 90
    x, y = np.meshgrid(np.linspace(-.54,.54,width,dtype=np.float32),
                       np.linspace(.54*height/width,-.54*height/width,height,dtype=np.float32))
    partial = out.with_name(out.stem+'.part.mp4')
    command = ['ffmpeg','-v','error','-nostdin','-y','-f','rawvideo','-pix_fmt','rgb24',
               '-s',f'{width}x{height}','-framerate',str(fps),'-i','-',
               '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',str(count/fps),
               '-c:v','libx264','-threads','2','-preset','fast','-crf','17','-pix_fmt','yuv420p',
               '-c:a','aac','-movflags','+faststart',str(partial)]
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
            for index in range(count):
                encoder.stdin.write(frame((x,y),index/fps).tobytes())
            encoder.stdin.close()
            if encoder.wait():
                raise RuntimeError('Optical insert did not encode.')
        partial.replace(out)
    finally:
        partial.unlink(missing_ok=True)
    out.with_suffix('.json').write_text(json.dumps({
        'source':'procedural', 'event_seconds':1.14, 'resolution':[width,height], 'frames':count,
        'input_assets':[], 'diegetic_sound':'Use the preserved courtyard bed from the scene.',
        'cut_intent':'Carry the lit rim into the visual insert. The aperture bends into rings after contact, giving the change in access a visible consequence.'},
        ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--small',action='store_true')
    args=parser.parse_args()
    render(args.output,*( (768,432) if args.small else (1536,864)))


if __name__=='__main__':
    main()
