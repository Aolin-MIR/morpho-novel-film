"""CPU mastering of a locked picture. Keep its timestamps, channel layout and source master."""
import json
import subprocess
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d, percentile_filter
from scipy.signal import welch

RATE = 48000
BLOCK = RATE // 10


def ff(*args, **kwargs):
    return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)], check=True, **kwargs)


def decode(path, rate=RATE):
    result = ff('-i', path, '-map', '0:a:0', '-af', f'aresample={rate}:async=1000:first_pts=0',
                '-ac', 2, '-f', 'f32le', '-', stdout=subprocess.PIPE)
    return np.frombuffer(result.stdout, np.float32).reshape(-1, 2)


def levels(sound, block):
    frames = sound[:len(sound)//block*block].reshape(-1, block, 2)
    return 10*np.log10(1e-14 + np.mean(frames*frames, axis=(1, 2)))


def inspect(sound, rate):
    values = levels(sound, rate//5)
    return dict(quiet_seconds=round(np.count_nonzero(values < -60)/5, 1),
                peak_dbfs=round(float(20*np.log10(np.max(abs(sound))+1e-12)), 2))


def room_texture(scene, meters, size, rng):
    # Use the median spectral shape from the quietest nonempty windows of this scene.
    # Discard phoneme timing, pitch peaks and transients instead of looping words.
    active = np.flatnonzero(meters > -74)
    if not len(active):
        return None
    selected = active[np.argsort(meters[active])[:max(1, len(active)//6)]]
    windows = np.concatenate([scene[i*BLOCK:(i+1)*BLOCK] for i in selected[:80]])
    freq, power = welch(windows, fs=RATE, nperseg=1024, axis=0, average='median')
    power = gaussian_filter1d(np.log(np.maximum(power, 1e-16)), 3, axis=0)
    shape = np.exp(power/2)
    shape *= (freq/(freq+100))[:, None] ** 2 / (1+(freq[:, None]/3300)**3)
    # A finite impulse response gives continuous air, even across long gaps.
    impulse = np.fft.fftshift(np.fft.irfft(shape, axis=0), axes=0) * np.hanning(1024)[:, None]
    from scipy.signal import oaconvolve
    common = rng.standard_normal(size).astype('f')
    channels = []
    for ch in range(2):
        noise = .8*common + .6*rng.standard_normal(size).astype('f')
        channels.append(oaconvolve(noise, impulse[:, ch].astype('f'), mode='same'))
    bed = np.column_stack(channels)
    return bed * (.008 / max(1e-12, np.sqrt(np.mean(bed*bed))))


def envelope(values, size):
    centers = (np.arange(len(values))+.5)*BLOCK
    return np.interp(np.arange(size), centers, values).astype('f')[:, None]


def master(source, destination, scenes, duration, matched=None, ambient_source=None):
    raw = decode(source)
    size = round(RATE*duration)
    sound = np.zeros((size, 2), np.float32)
    sound[:min(size,len(raw))] = raw[:size]
    before = inspect(sound, RATE)
    rng = np.random.default_rng(117)
    if matched is not None:
        sound += matched
    for scene in scenes:
        start, end = [round(scene[key]*RATE) for key in ('start','end')]
        region = sound[start:end]
        meters = levels(region, BLOCK)
        if not len(meters):
            continue
        # Ride phrase-level changes gently; leave syllable articulation and stereo intact.
        foreground = percentile_filter(meters, 80, size=13, mode='nearest')
        gain = np.clip((-19.5-foreground)*.68, -6, 6) * np.clip((foreground+41)/10, 0, 1)
        gain = gaussian_filter1d(gain, 2)
        bed = room_texture(region, meters, len(region), rng)
        if bed is None and ambient_source is not None:
            bed = room_texture(ambient_source, levels(ambient_source,BLOCK), len(region), rng)
        region *= envelope(10**(gain/20), len(region))
        if bed is not None:
            weight = gaussian_filter1d(np.clip((-33-meters)/15, 0, 1), 1)
            bed *= envelope(weight, len(region))
            edge = min(RATE//20,len(bed)//2)
            bed[:edge] *= np.linspace(0,1,edge)[:,None]
            bed[-edge:] *= np.linspace(1,0,edge)[:,None]
            region += bed
    # Stereo-linked soft knee leaves ordinary samples unchanged.
    for start in range(0,size,RATE):
        region = sound[start:start+RATE]
        peak = np.max(abs(region),axis=1,keepdims=True)
        eased = np.minimum(peak,.65)+.21*np.tanh(np.maximum(peak-.65,0)/.21)
        region *= eased/np.maximum(peak,1e-12)
    ff('-y','-i',source,'-f','f32le','-ar',RATE,'-ac',2,'-i','-','-map','0:v:0','-map','1:a:0',
       '-c:v','copy','-c:a','aac','-b:a','256k','-movflags','+faststart',destination,input=sound.tobytes())
    return before, inspect(decode(destination), RATE)
