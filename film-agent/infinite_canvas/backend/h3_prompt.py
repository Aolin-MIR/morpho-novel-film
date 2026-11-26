"""Compile a shot's scoped references and native performance for MiniMax H3 Ref2VA."""
from copy import deepcopy
from math import isfinite
import re

from continuity_prompt_policy import COMPOSITION_SCOPES, POSITIVE_STAGING, VERSION
from film_continuity import plate_notes
from film_speech import H3_SPEECH

REFERENCE_ROLES = {
    'character': 'appearance, costume construction and silhouette',
    'location': 'scene geography, fixed landmarks and lighting direction',
    'prop': 'physical material, silhouette and scale',
    'composition': 'opening composition and current positions',
}
SCOPED_ROLES = {
    'character': 'identity, the visible facial geometry, body proportions and the established garment construction',
    'location': 'fixed geometry, landmarks, surface texture and the direction of the key light',
    'prop': 'that single object’s silhouette, physical scale, material and identifying marks',
}


def with_current_prompt_policy(shot):
    if 'continuity' not in shot:
        return shot
    result = deepcopy(shot)
    result['continuity'].setdefault('prompt_policy', VERSION)
    return result


def production_text(value):
    if not isinstance(value, str) or not value.strip() or re.search(r'<\s*/?\s*d\b', value, re.I):
        raise ValueError('画面、摄影和声音说明须独立于对白标签。')
    return value.strip()


def compile_reference_prompt(shot, duration, picture_count, audio_count, motion):
    if not isfinite(duration) or not 4 <= duration <= 15:
        raise ValueError('H3 镜头时长须为 4–15 秒。')
    pictures = shot['pictures']
    if len(pictures) != picture_count or not pictures:
        raise ValueError('H3 参考图必须逐张填写实际用途。')
    continuity = shot.get('continuity', {})
    if not isinstance(continuity, dict):
        raise ValueError('续拍镜头须提供完整的连续性规格。')
    policy = continuity.get('prompt_policy')
    if policy is not None and policy != VERSION:
        raise ValueError('镜头登记了未支持的连续性提示版本。')
    scoped = policy == VERSION
    notes = plate_notes(shot, duration, staged=scoped)
    definitions, retention, visible = [], [], {}
    for index, image in enumerate(pictures, 1):
        role = image['role']
        if role not in REFERENCE_ROLES:
            raise ValueError('参考图需要明确人物、空间、道具或本镜构图用途。')
        picture = f'<Picture {index}>'
        subject = picture if role == 'composition' else f'<Subject {index}>'
        visible[index] = subject
        meaning = production_text(image['use'])
        if not scoped and 'scope' in image:
            raise ValueError('局部参考职责须使用当前连续性提示版本。')
        if scoped:
            if role == 'composition':
                scope = image.get('scope', 'layout')
                if scope not in COMPOSITION_SCOPES:
                    raise ValueError('构图参考须使用已定义的画面、体形或衣着职责。')
                purpose = COMPOSITION_SCOPES[scope]
            elif role == 'character' and image.get('scope') == 'identity':
                purpose = 'stable facial identity, age and visible facial geometry only. Outfit, light and pose follow the shot'
            else:
                if 'scope' in image:
                    raise ValueError('人物仅可指定固定身份职责，其他局部职责用于构图参考。')
                purpose = SCOPED_ROLES[role]
            source = f'{subject} from {picture}' if subject != picture else picture
            definitions.append(f'{source}: {meaning}. Scope: {purpose}.')
            retention.append(f'{subject}: fully_preserved - Preserve only {purpose}.')
        else:
            definitions.append(f'{subject} from {picture} supplies {meaning} for {REFERENCE_ROLES[role]}.'
                               if subject != picture else f'{picture} supplies {meaning} for opening composition.')
            retention.append(f'{subject}: partially_preserved - Retain {REFERENCE_ROLES[role]} only.')
    speech = shot.get('speech', [])
    speaker_ids = {}
    lines = []
    previous_time = -1
    for line in speech:
        index = line['picture']
        at = line['at']
        if type(index) is not int or not 1 <= index <= picture_count or pictures[index - 1]['role'] != 'character':
            raise ValueError('画内发声须绑定到已编号的角色参考。')
        if not isfinite(at) or not previous_time < at < duration:
            raise ValueError('对白入点必须位于本镜内并按实际发生顺序排列。')
        previous_time = at
        speaker = speaker_ids.setdefault(index, f'S{len(speaker_ids) + 1}')
        action = production_text(line['delivery'])
        text = production_text(line['text'])
        window = line.get('window') if 'continuity' in shot else None
        timing = (f'Between {window[0]:g} and {window[1]:g} seconds, starting at {at:g} seconds'
                  if window else f'At {at:g} seconds')
        lines.append(f'{timing}, {visible[index]} ({speaker}) {action} and says exactly once: '
                     f'<d>[Chinese] {text}</d>.')
    voice_links = shot.get('voices', [])
    if speech:
        lines.append(H3_SPEECH)
    if notes:
        if speech:
            lines.append('Only the named speaker articulates the line; visible listeners react silently.')
        else:
            lines.append('Silent physical reactions, with natural resting mouths.')
        if not scoped:
            lines.append('Retain costume cut, fabric base color and held objects throughout except for the listed actions. '
                         'Air and surfaces stay clear except for visible sources specified by the action.')
            lines.append(production_text(notes))
    if len(voice_links) != audio_count or len(voice_links) != len(set(voice_links)):
        raise ValueError('每条音色参考须绑定到一个实际说话者。')
    for number, index in enumerate(voice_links, 1):
        if index not in speaker_ids:
            raise ValueError('音色参考指向的角色在本镜没有对白。')
        audio = f'<Audio {number}>'
        name = f'{visible[index]} ({speaker_ids[index]})'
        definitions.append(f'{audio} is the voice-timbre reference for {name}.')
        retention.append(f'{audio}: reference - use the timbre only; the recorded words are never reused.')
        lines.append(f'{name} uses the timbre of {audio}.')
    if motion:
        definitions.append('<Video 1> supplies the movement phases and body weight.')
        retention.append(
            '<Video 1>: fully_preserved - Movement timing, contact and weight only. '
            'Identity, wardrobe and place come from their assigned picture references.'
            if scoped else '<Video 1>: partially_preserved - Follow the action timing and contact.')
    spaces = [visible[i] for i, image in enumerate(pictures, 1) if image['role'] == 'location']
    if spaces:
        lines.append('Scene geography and light come from ' + ', '.join(spaces) + '.')
    description = (
        f'{production_text(shot["style"])}\n'
        f'[Shot 1] A single {duration:g}-second shot. {production_text(shot["opening"])} '
        + (production_text(notes) + ' ' + POSITIVE_STAGING + ' ' if scoped else '')
        + f'{production_text(shot["action"])} {production_text(shot["camera"])} '
        + ' '.join(lines) + ' The shot ends on this visible consequence: '
        + production_text(shot['outcome'])
    )
    soundscape = production_text(shot['soundscape'])
    if 'continuity' in shot:
        soundscape += ' Use only the stated diegetic sounds and scripted speech. No score or narrator in the take.'
    fields = (
        ('subject_definitions', '\n'.join(definitions)),
        ('summary', '[reference generation] The shot follows the specified opening, performance and ending.'),
        ('retention_analysis', '\n'.join(retention)),
        ('detailed_description', description),
        ('overall_soundscape', soundscape),
        ('non_diegetic_music', 'N/A'),
    )
    result = '\n\n'.join(f'{key}:\n{value}' for key, value in fields)
    if len(result) > 5000:
        raise ValueError('已超过 H3 生产提示的长度限制。')
    return result
