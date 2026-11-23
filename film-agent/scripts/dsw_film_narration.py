"""Record low Mandarin narration for a DSW film project with Qwen3-TTS."""
import argparse
import json
from pathlib import Path
import re
import sys

import numpy as np
from qwen_tts import Qwen3TTSModel
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))
from film_speech import NATURAL_SPEECH, speech_units

WORKSPACE = Path('/mnt/AIRSSD/user_workspace/liaolin2')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    args = parser.parse_args()
    source = args.timeline.resolve(strict=True)
    project = source.parent.parent
    if project.parent != WORKSPACE / 'morpho-personal/projects':
        raise ValueError('Narration must belong to a film project in the personal workspace.')
    output = project / 'renders' / 'narration'
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(source.read_text(encoding='utf-8'))
    cues = config['cues']
    voice = config.get('speaker', 'Uncle_Fu')
    direction = config.get('direction', '音色低沉，近讲，保留真人音色粗细和动作感。')
    if not isinstance(direction, str) or not direction.strip():
        raise ValueError('旁白需要声音和表演说明。')
    if any(not re.fullmatch('[a-z][a-z0-9-]{0,48}', cue['id']) for cue in cues):
        raise ValueError('Unexpected narration cue ID.')
    pending = [cue for cue in cues if not (output / (cue['id'] + '.wav')).is_file()]
    if not pending:
        return
    torch.set_num_threads(4)
    model = Qwen3TTSModel.from_pretrained(
        str(WORKSPACE / 'audio_drama/models/Qwen3-TTS-12Hz-1.7B-CustomVoice'),
        device_map='cpu', dtype=torch.float32, attn_implementation='sdpa', local_files_only=True)
    for cue in pending:
        torch.manual_seed(8721)
        waves, rate = model.generate_custom_voice(
            text=cue['text'], language='Chinese', speaker=voice, max_new_tokens=350,
            instruct=f"{direction} {cue.get('delivery', '')} {NATURAL_SPEECH}")
        audio = np.asarray(waves[0], dtype=np.float32)
        if not np.isfinite(audio).all() or rate != 24000 or audio.size < 8000:
            raise ValueError(f'Invalid audio: {cue["id"]}')
        active = np.flatnonzero(abs(audio) >= max(.004, float(abs(audio).max()) * .012))
        if not active.size:
            raise ValueError(f'Silent audio: {cue["id"]}')
        clip = audio[max(0, active[0] - 1400):active[-1] + 2500]
        out = output / (cue['id'] + '.wav')
        temporary = out.with_suffix('.part')
        sf.write(temporary, clip, rate, format='WAV', subtype='PCM_16')
        if sf.info(temporary).frames != clip.size:
            raise ValueError('Narration encoding incomplete.')
        temporary.replace(out)
        active_seconds = (active[-1] - active[0] + 1) / rate
        print(cue['id'], f'{clip.size/rate:.2f}s',
              f'{speech_units(cue["text"]) / active_seconds:.2f} 字/秒（含句内停顿）', flush=True)


if __name__ == '__main__':
    main()

