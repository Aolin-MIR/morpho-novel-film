import json
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dsw_film_agent import json_text
from film_identity_lock import VERSION, bind, verify
from media_segments import fingerprint


def test_phase_faces_resolve_to_same_untouched_identity_board(tmp_path):
    agent=SimpleNamespace(ROOT=tmp_path,personal_file=Path,json_text=json_text)
    basedir=tmp_path/'identity-locks'
    basedir.mkdir()
    card=basedir/'person.png'
    Image.fromarray(np.random.default_rng(37).integers(0,255,(192,192,3),dtype=np.uint8)).save(card)
    profile={'policy':VERSION,'version':'a','project':'demo','cast':[
        {'id':'lead','aliases':['旅人'],'reference':str(card),'sha256':fingerprint(card),
         'face_box':[10,10,170,170],'geometry':'同一年龄与眉眼'}]}
    (basedir/'demo.json').write_text(json.dumps(profile))
    submitted=[]
    for phase in ('发觉事态的近景','被关押时的近景','说完后看同行者'):
        path=basedir/f'{phase}.json'
        path.write_text(json.dumps({'opening':'褐衣，微亮','pictures':[
            {'role':'character','use':f'旅人｜{phase}'},{'role':'character','use':'大蝎｜回看'}],
            'continuity':{}}))
        original={'shot_contract':str(path),'references':[str(basedir/f'{phase}.png'),'unchanged']}
        updated=bind(agent,'demo',original)
        verify(agent,updated)
        shot=json.loads(Path(updated['shot_contract']).read_text())
        assert shot['pictures'][0]['scope']=='identity'
        assert shot['opening']=='褐衣，微亮'
        assert updated['references']==[str(card),'unchanged']
        submitted.append(updated)
    card.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='派单前锁定'):
        verify(agent,submitted[0])


def test_queue_sends_the_bound_image_and_compiles_identity_scope(tmp_path, monkeypatch):
    import dsw_film_agent as agent
    import render_h3_pilot

    monkeypatch.setattr(agent, 'ROOT', tmp_path)
    monkeypatch.setattr(agent, 'WORKSPACE', tmp_path)
    locks = tmp_path/'identity-locks'
    locks.mkdir()
    image = Image.fromarray(np.random.default_rng(41).integers(0, 255, (192, 192, 3), dtype=np.uint8))
    canonical, alternate = (tmp_path/name for name in ('canonical.png', 'alternate.png'))
    image.save(canonical)
    image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(alternate)
    (locks/'demo.json').write_text(json.dumps({
        'policy':VERSION, 'project':'demo', 'version':'v1',
        'cast':[{'id':'traveler', 'aliases':['旅人'], 'reference':str(canonical),
                 'sha256':fingerprint(canonical), 'face_box':[10,10,170,170], 'geometry':'同一脸型'}],
    }))
    contract = tmp_path/'shot.json'
    contract.write_text(json.dumps({
        'style':'同一绘画质感', 'opening':'褐色外套，坐在桌边', 'action':'旅人抬眼',
        'outcome':'旅人仍坐在桌边', 'camera':'固定近景', 'soundscape':'室内底噪',
        'pictures':[{'role':'character','use':'旅人｜低光'}],
        'continuity':{'scene':'study', 'before':{'褐衣':'完整'}, 'after':{'褐衣':'完整'}, 'changes':{},
                      'beat':{'question':'他是否察觉对方','gain':'抬眼','window':[0,4]}},
    }))
    with agent.connection() as db:
        identifier = agent.submit(db, {'project':'demo', 'kind':'render', 'inputs':{
            'shot_contract':str(contract), 'references':[str(alternate)], 'duration':4, 'size':'1536x864',
            'quality':'final', 'reference_lineage':[{'picture':1, 'plate_sha256':fingerprint(alternate),
                                                     'source_sha256':fingerprint(alternate)}]}})
        queued = json.loads(db.execute("SELECT inputs FROM jobs WHERE id=?", (identifier,)).fetchone()[0])

    assert queued['reference_lineage'] == []
    assert queued['source_versions'][str(canonical)] == fingerprint(canonical)
    assert str(alternate) not in queued['source_versions']
    seen = {}

    def capture(arguments):
        from h3_prompt import compile_reference_prompt

        path = Path(arguments[arguments.index('--shot-contract') + 1])
        seen['prompt'] = compile_reference_prompt(json.loads(path.read_text()), 4, 1, 0, False)
        seen['reference'] = arguments[arguments.index('--reference') + 1]
        output = Path(arguments[-1])
        output.write_bytes(b'test media')
        output.with_suffix('.job.json').write_text('{"id":"render-test"}')

    monkeypatch.setattr(render_h3_pilot, 'main', capture)
    monkeypatch.setattr(agent, 'probe', lambda _: {'streams':[
        {'codec_type':'video', 'width':1536, 'height':864}, {'codec_type':'audio'}]})
    result = agent.render(queued, tmp_path)
    assert seen['reference'] == str(canonical)
    assert 'stable facial identity, age and visible facial geometry only' in seen['prompt']
    assert result['identity_lock']['cast'][0]['sha256'] == fingerprint(canonical)
