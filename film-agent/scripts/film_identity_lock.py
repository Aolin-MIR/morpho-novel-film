"""Bind recurring cast to a versioned face reference at the real render entry point."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from media_segments import fingerprint

VERSION = 'canonical-character-reference/v1'


def registry(agent, project):
    return agent.ROOT / 'identity-locks' / f'{project}.json'


def matches(label, alias):
    return label==alias or any(label.startswith(alias+marker) for marker in
                                ('｜','（',' (','|','：',':','，','。',' '))


def bind(agent, project, inputs):
    path=registry(agent,project)
    if not path.is_file():
        return inputs
    if 'shot_contract' not in inputs:
        raise ValueError('本项目已登记角色身份。新镜头需使用逐图指定用途的拍摄单。')
    source=agent.personal_file(inputs['shot_contract'])
    shot=json.loads(source.read_text(encoding='utf-8'))
    refs=deepcopy(inputs['references'])
    policy=json.loads(path.read_text(encoding='utf-8'))
    if policy['policy']!=VERSION or policy['project']!=project:
        raise ValueError('角色身份登记与当前影片不一致。')
    if len(shot['pictures'])!=len(refs):
        raise ValueError('人物参考与逐图用途不匹配。')
    from continuity_prompt_policy import VERSION as H3_POLICY

    changes=[]
    for index,slot in enumerate(shot['pictures']):
        if slot['role']!='character':
            continue
        candidates=[row for row in policy['cast'] if any(matches(slot['use'],alias) for alias in row['aliases'])]
        if not candidates:
            continue
        if len(candidates)!=1:
            raise ValueError('同一参考槽匹配了多个已锁定角色。')
        actor=candidates[0]
        card=agent.personal_file(actor['reference'])
        if fingerprint(card)!=actor['sha256']:
            raise ValueError('锁定的角色底片发生变化，须先登记并审看新身份板。')
        refs[index]=str(card)
        label=actor['aliases'][0]
        from film_reference_checks import check_reference

        slot.update(scope='identity',people='single',face_boxes=[actor['face_box']])
        check_reference(slot,card)
        if not isinstance(shot.get('continuity'),dict):
            raise ValueError('锁定角色要求带有镜头连续性规格和分离后的服装信息。')
        shot['continuity']['prompt_policy']=H3_POLICY
        slot['use']=f'{label}｜固定身份板。{actor["geometry"]}。照片只提供人物身份与五官，不沿用拍摄背景或阶段服装。'
        changes.append({'character':actor['id'],'slot':index+1,'reference':str(card),'sha256':actor['sha256']})
    if not changes:
        return inputs
    sign=sha256(agent.json_text([shot,changes,policy['version']]).encode()).hexdigest()
    directory=agent.ROOT/'identity-locks'/'contracts'/project
    directory.mkdir(parents=True,exist_ok=True)
    actual=directory/f'{sign}.json'
    if actual.is_file():
        if json.loads(actual.read_text(encoding='utf-8')) != shot:
            raise ValueError('已登记的身份拍摄单内容与编译结果不一致。')
    else:
        with NamedTemporaryFile(dir=directory, mode='w', encoding='utf-8', delete=False) as file:
            temporary = Path(file.name)
            json.dump(shot, file, ensure_ascii=False, indent=2)
            file.write('\n')
        try:
            temporary.replace(actual)
        finally:
            temporary.unlink(missing_ok=True)
    bound = {**inputs, 'shot_contract':str(actual), 'references':refs,
             'identity_lock':{'policy':VERSION,'version':policy['version'],'cast':changes,
                              'contract_sha256':fingerprint(actual)}}
    if 'reference_lineage' in inputs:
        slots = {item['slot'] for item in changes}
        bound['reference_lineage'] = [entry for entry in inputs['reference_lineage']
                                      if not isinstance(entry, dict) or entry.get('picture') not in slots]
    return bound


def verify(agent, inputs):
    lock=inputs.get('identity_lock')
    if not lock:
        return
    if fingerprint(agent.personal_file(inputs['shot_contract']))!=lock['contract_sha256']:
        raise ValueError('实际派单的身份规格发生变化。')
    for actor in lock['cast']:
        ref=inputs['references'][actor['slot']-1]
        if ref!=actor['reference'] or fingerprint(agent.personal_file(ref))!=actor['sha256']:
            raise ValueError('角色参考图和派单前锁定的原图不一致。')
