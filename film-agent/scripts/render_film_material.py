"""Render a source-registered material effect on the exact approved edit window."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


FPS = 24
LUMA = np.array([.2126, .7152, .0722], dtype=np.float32)


def ramp(value, lo, hi):
    amount = np.clip((value - lo) / (hi - lo), 0, 1)
    return amount * amount * (3 - 2 * amount)


def mask_polygon(size, points, softness):
    width, height = size
    matte = Image.new('L', size)
    ImageDraw.Draw(matte).polygon([(int(x * width), int(y * height)) for x, y in points], fill=255)
    return np.asarray(matte.filter(ImageFilter.GaussianBlur(softness * height)), dtype=np.float32) / 255


def leaf_response(spec, size, x, y):
    mask = mask_polygon(size, spec['surface'], .012)
    start_x, start_y = spec['origin']
    axis_x, axis_y = spec['axis']
    width, height = size
    gx, gy = (x - start_x) * (width / height), y - start_y
    travel = (gx * axis_x + gy * axis_y)
    cross = gx * axis_y - gy * axis_x
    fiber = (np.sin(travel * 68 + np.cos(cross * 43) * .8) + np.sin(cross * 96 + travel * 61)) * .055

    def render(rgb, time):
        strength = ramp(time, spec['onset'], spec['peak']) * (1 - ramp(time, spec['release'], spec['end']))
        if strength < 1e-4:
            return rgb
        # Colour selects the payload surface and preserves foreground fingers and the cooler forest floor.
        tan = ramp(rgb[..., 0] - rgb[..., 1], 12, 38) * ramp(rgb[..., 1] - rgb[..., 2], 9, 38)
        fill = (1 - ramp(travel, strength * .70 + fiber - .16, strength * .72 + fiber + .16))
        sheen = np.exp(-((travel - strength * .53 - fiber) / (.05 + .06 * strength)) ** 2)
        sheen *= ramp(rgb[..., 0], 90, 178) * (1 - ramp(np.abs(cross), .12, .27))
        amount = (fill * .50 + sheen * .56) * strength * tan * mask * spec['strength']
        # Combine internal warm scattering and a less saturated directional surface reflection.
        transport = rgb * np.array([.38, .24, .14], dtype=np.float32) + [23, 15, 7]
        return rgb + amount[..., None] * transport

    return render


def river_response(spec, size, x, y):
    width, height = size
    x0, y0, x1, y1 = spec['source_roi']
    tile = (slice(round(y0 * height), round(y1 * height)), slice(round(x0 * width), round(x1 * width)))
    x_px, y_px = x * width, y * height
    # Low-amplitude waves at different frequencies keep highlight breakup both broad and fine.
    response_colour = np.array([1., .54, .19], dtype=np.float32)

    def render(rgb, time):
        roi = rgb[tile]
        warm = ramp(roi[..., 0], 170, 245) * ramp(roi[..., 0] - roi[..., 2], 60, 118)
        mass = float(warm.sum())
        if mass < .1:
            raise ValueError(f'The practical light source was not visible in the approved tracking window at {time:.3f}s.')
        px = float(np.sum(x_px[:, tile[1]] * warm) / mass) / width
        py = float(np.sum(y_px[tile[0], :] * warm) / mass) / height
        depth = y - py
        sway = .0016 * np.sin(40 * depth + 3.4 * time) + .0011 * np.cos(83 * depth - 4.8 * time)
        dy = np.maximum(depth, 0)
        ripple = np.sin(441 * depth - time * 5.8 + np.sin((x - px) * 150) * .75)
        swell = np.sin(199 * depth + time * 4.3 + np.sin((x - px) * 97 - time) * .55)
        lobe = (np.maximum(0, .67 * ripple + .28 * swell)) ** 2.1
        width_at_depth = .003 + dy * .064
        spread = np.exp(-((x - px - sway) / width_at_depth) ** 2)
        glow = np.exp(-((x - px) / (.009 + dy * .04)) ** 2)
        falloff = np.exp(-dy * 15) * ramp(depth, .008, .024)
        end = 1 - ramp(depth, spec['depth'] - .025, spec['depth'])
        neutral = 1 - ramp(rgb[..., 0] - rgb[..., 2], 12, 65) * .5
        amount = ((lobe * 53 + 5) * spread + 4 * glow) * falloff * end * neutral * spec['strength']
        return rgb + amount[..., None] * response_colour

    return render


def shadow_response(spec, size, x, y):
    mask = mask_polygon(size, spec['surface'], .013)
    softening = ramp(y, .65, .98)
    colour = np.array([1., .97, .90], dtype=np.float32)
    baseline = None

    def render(rgb, time):
        nonlocal baseline
        luma = rgb @ LUMA
        if baseline is None:
            baseline = luma.copy()
        if time < spec['onset']:
            return rgb
        difference = np.clip(baseline - luma, 0, 52)
        shadow = Image.fromarray(difference.astype(np.uint8))
        soft = np.asarray(shadow.filter(ImageFilter.GaussianBlur(size[1] * .006)))
        graded = (1 - softening) * difference + softening * soft
        weight = ramp(difference, 4, 17) * mask * ramp(time, spec['onset'], spec['peak'])
        # Retain the same contact shape, stone pores and first two shadows.
        fill = (difference - graded * 1.26) * spec['strength'] * weight
        return rgb + fill[..., None] * colour

    return render


def aperture_response(spec, size, x, y):
    """Register a refracted image to the water inside a fixed vessel."""
    center_x, center_y, radius_x, radius_y = spec['ellipse']
    u, v = (x-center_x)/radius_x, (y-center_y)/radius_y
    surface = 1-ramp(np.hypot(u,v), .81, .98)
    # Slight movement in the highlight follows the wavefront, not the actor or the pot.
    distance = np.hypot(u+.62,v+.07)
    mineral = (np.cos(34*u+13*v+np.sin(19*v)*.8)*np.sin(46*v)+1)*.5

    def render(rgb, time):
        opacity = ramp(time, spec['onset'], spec['peak'])
        if opacity < 1e-4:
            return rgb
        phase = distance-1.5*(time-spec['onset'])
        travel = .072*np.sin(23*phase)*np.exp(-(phase/.44)**2)
        px, py = u-.29+travel, v+.09-travel*.36
        disc_radius = np.hypot(px,py)
        curve = np.sqrt(np.maximum(0,1-(disc_radius/.56)**2))
        globe = 1-ramp(disc_radius,.49,.56)
        bands = .57+.20*np.sin(py*18+curve*1.9)+.13*np.sin(py*38+px*7)
        reflectivity = ramp(rgb@LUMA, 124, 221)
        strength = spec['strength']*surface*reflectivity*opacity*globe
        warm = np.stack((147+54*bands, 79+34*bands, 46+19*bands), axis=-1)
        warm += (mineral*6 + curve*16)[...,None]
        return rgb + (warm-rgb)*strength[...,None]

    return render


RENDERERS = {'leaf-transmission': leaf_response, 'water-reflection': river_response,
             'shadow-penumbra': shadow_response, 'water-aperture': aperture_response}


def validate(spec, duration):
    if spec['kind'] not in RENDERERS or not 0 < spec['strength'] <= 1.4:
        raise ValueError('Material response and bounded strength are required.')
    if spec['kind'] == 'water-reflection':
        x0, y0, x1, y1 = spec['source_roi']
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and 0 < spec['depth'] < 1 - y1):
            raise ValueError('The light and receiving water surface must be in the frame.')
        return
    if spec['kind'] == 'water-aperture':
        x, y, width, height = spec['ellipse']
        if not (0 <= x-width < x+width <= 1 and 0 <= y-height < y+height <= 1
                and 0 <= spec['onset'] < spec['peak'] < duration):
            raise ValueError('Aperture must stay inside the approved water surface and cut.')
        return
    if len(spec['surface']) < 3 or any(len(point) != 2 or not all(0 <= value <= 1 for value in point)
                                      for point in spec['surface']):
        raise ValueError('A stable projected receiving surface is required.')
    if not 0 <= spec['onset'] < spec['peak'] < duration:
        raise ValueError('Invalid material event times.')
    if spec['kind'] == 'leaf-transmission':
        if not spec['peak'] <= spec['release'] < spec['end'] <= duration:
            raise ValueError('The leaf transmission must resolve within the cut.')
        if len(spec['origin']) != 2 or len(spec['axis']) != 2 or abs(np.hypot(*spec['axis']) - 1) > .04:
            raise ValueError('The light front requires an origin and a unit direction.')


def run(source, spec, output):
    raw_info = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                       '-show_entries', 'stream=width,height,r_frame_rate,nb_frames',
                                       '-of', 'json', str(source)], text=True, timeout=20)
    info, = json.loads(raw_info)['streams']
    start, end = spec['source_frames']
    if (info['r_frame_rate'] != '24/1' or not isinstance(start, int) or not isinstance(end, int)
            or not 0 <= start < end <= int(info['nb_frames']) or end - start > 600):
        raise ValueError('Provide a valid 24 fps source interval of up to 600 frames.')
    if sha256(source.read_bytes()).hexdigest() != spec['source_sha256']:
        raise ValueError('This grade belongs to a different source plate.')
    width, height = size = info['width'], info['height']
    duration = (end - start) / FPS
    validate(spec, duration)
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    transform = RENDERERS[spec['kind']](spec, size, x, y)
    read = ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-ss', f'{start / FPS:.9f}',
            '-i', str(source), '-frames:v', str(end - start), '-an', '-pix_fmt', 'rgb24', '-threads', '2',
            '-f', 'rawvideo', '-']
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix='candidate-') as tmp:
        staged = Path(tmp) / 'effect.mp4'
        write = ['ffmpeg', '-v', 'error', '-nostdin', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                 '-s', f'{width}x{height}', '-r', str(FPS), '-i', '-', '-ss', f'{start / FPS:.9f}',
                 '-i', str(source), '-map', '0:v:0', '-map', '1:a:0?', '-t', f'{duration:.9f}',
                 '-c:a', 'aac', '-b:a', '160k', '-pix_fmt', 'yuv420p', '-c:v', 'libx264',
                 '-crf', '16', '-preset', 'fast', '-threads', '2', '-movflags', '+faststart', str(staged)]
        with subprocess.Popen(read, stdout=subprocess.PIPE) as decoder, subprocess.Popen(write, stdin=subprocess.PIPE) as encoder:
            for index in range(end - start):
                data = decoder.stdout.read(width * height * 3)
                if len(data) != width * height * 3:
                    raise ValueError('The source did not decode the exact approved frame interval.')
                rgb = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3).astype(np.float32)
                graded = transform(rgb, index / FPS)
                encoder.stdin.write(np.clip(graded, 0, 255).astype(np.uint8).tobytes())
            encoder.stdin.close()
            if decoder.wait(timeout=30) or encoder.wait(timeout=30):
                raise RuntimeError('The source or effect failed to encode.')
        staged.replace(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('spec', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.source, json.loads(args.spec.read_text(encoding='utf-8')), args.output)
