"""Render a timed Mandarin narration using a verified audio-drama casting reference."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

import numpy as np
from qwen_tts import Qwen3TTSModel
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
from film_speech import speech_units


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    parser.add_argument('reference_bank', type=Path)
    parser.add_argument('model', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args()
    timeline = json.loads(args.timeline.read_text(encoding='utf-8'))
    manifest = args.reference_bank.resolve(strict=True)
    bank = json.loads(manifest.read_text(encoding='utf-8'))['references']
    ref, = [item for item in bank if item['pid'] == timeline['reference_id']]
    audio = (manifest.parent / ref['extended_audio']).resolve(strict=True)
    if not audio.is_relative_to(manifest.parent):
        raise ValueError('Approved voice file is outside its casting bank.')
    digest = sha256(audio.read_bytes()).hexdigest()
    if digest != ref['extended_audio_sha256']:
        raise ValueError('Approved voice file differs from the casting receipt.')
    cues = timeline['cues']
    identifiers = {cue['id'] for cue in cues}
    if len(identifiers) != len(cues) or any(not re.fullmatch('[a-z][a-z0-9-]{0,63}', key) for key in identifiers):
        raise ValueError('Narration timeline has a duplicate or unsafe cue ID.')
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = {'reference_id': ref['pid'], 'reference_sha256': digest,
               'timeline_sha256': sha256(args.timeline.read_bytes()).hexdigest(), 'model': args.model.name}
    receipt_path = args.output / 'voice.json'
    if receipt_path.exists() and json.loads(receipt_path.read_text()) != receipt:
        raise ValueError('This directory contains a different recording session.')
    receipt_path.write_text(json.dumps(receipt, indent=2))
    pending = [cue for cue in cues if not (args.output / (cue['id'] + '.wav')).is_file()]
    if not pending:
        return
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(2)
    model = Qwen3TTSModel.from_pretrained(
        str(args.model.resolve(strict=True)), device_map='cpu', dtype=torch.float32,
        attn_implementation='sdpa', local_files_only=True)
    prompt = model.create_voice_clone_prompt(ref_audio=str(audio), ref_text=ref['text'], x_vector_only_mode=False)
    for cue in pending:
        torch.manual_seed(8721)
        outputs, rate = model.generate_voice_clone(
            text=cue['text'], language='Chinese', voice_clone_prompt=prompt, max_new_tokens=384)
        wave = np.asarray(outputs[0], dtype=np.float32)
        if not np.isfinite(wave).all() or rate != 24000 or wave.size < rate // 3:
            raise ValueError(f'Invalid narration output for {cue["id"]}.')
        active = np.flatnonzero(np.abs(wave) >= max(.0035, float(np.abs(wave).max()) * .012))
        if not active.size:
            raise ValueError(f'Silent narration output for {cue["id"]}.')
        wave = wave[max(0, active[0] - 1100):active[-1] + 2100]
        dest = args.output / (cue['id'] + '.wav')
        part = dest.with_suffix('.part')
        sf.write(part, wave, rate, format='WAV', subtype='PCM_16')
        part.replace(dest)
        active_seconds = (active[-1] - active[0] + 1) / rate
        print(cue['id'], f'{wave.size / rate:.2f}s',
              f'{speech_units(cue["text"]) / active_seconds:.2f} 字/秒（含句内停顿）', flush=True)


if __name__ == '__main__':
    main()
