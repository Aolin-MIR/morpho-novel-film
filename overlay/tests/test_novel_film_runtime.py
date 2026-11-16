import copy
from array import array
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infinite_canvas' / 'backend'))

import film_render
import film_score
import local_story_gateway
import novel_film
import rented_h3
import video_prompts


class NovelFilmRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_monochrome_grade_reduces_chroma_in_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'red.mp4', root / 'graded.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=128x72:d=1',
                            '-c:v', 'libx264', str(source)], check=True, timeout=15)
            with patch.object(film_render.workflow, 'ROOT', root):
                film_render.workflow.assemble_paths([source], output, '16:9', grades=['monochrome'])
            frame = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1',
                                             '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-'], timeout=10)
            self.assertLess(abs(frame[-100] - 128), 25)

    def test_video_model_review_budget_is_three_shots(self):
        shots = [{'id': str(index), 'spectacle': True} for index in range(10)]
        package = {'film': {'pilot_shot_id': '4'}, 'scenes': [{'shots': shots}]}
        self.assertEqual([shot['id'] for shot in shots if film_render.needs_model_review(shot, package)],
                         ['0', '4', '9'])

    def test_review_deadline_stops_without_retry(self):
        with patch.object(film_render.subprocess, 'run', side_effect=subprocess.TimeoutExpired('review', 90)) as call:
            with self.assertRaisesRegex(TimeoutError, '90 秒'):
                film_render.judge([], 'test-key')
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs['timeout'], 90)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_ordinary_take_is_checked_without_calling_omni(self):
        with tempfile.TemporaryDirectory() as directory:
            take = Path(directory) / 'shot.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=128x72:d=5',
                            '-f', 'lavfi', '-i', 'sine=f=440:d=5', '-shortest', str(take)], check=True, timeout=15)
            with patch.object(film_render, 'judge') as judge:
                verdict = film_render.review(take, {'id': 'ordinary', 'clip_seconds': 4},
                                             {'film': {'pilot_shot_id': 'pilot'}, 'options': {'aspect_ratio': '16:9'}},
                                             [], None, 'test-key')
            judge.assert_not_called()
            self.assertEqual((verdict['method'], verdict['in_seconds'], verdict['out_seconds']), ('local', 0, 4))

    def test_rejected_take_is_not_rejudged_when_resuming(self):
        job = {'reviews': []}
        take = Path('film-02-1.mp4')
        verdict = {'accepted': False, 'score': 50, 'note': '手形变形'}
        with patch.object(film_render, 'review', return_value=verdict) as review, \
             patch.object(film_render.workflow, 'put'):
            args = (job, take, {'id': 'two'}, {}, [], None, 'film-01-1.mp4', 'test-key', 1)
            self.assertEqual(film_render.review_take(*args), film_render.review_take(*args))
        review.assert_called_once()
        self.assertEqual(len(job['reviews']), 1)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_score_mixes_two_cues_onto_audio_without_changing_video_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, track, output = (root / name for name in ('source.mp4', 'music.wav', 'mixed.mp4'))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=128x72:d=2',
                            '-f', 'lavfi', '-i', 'sine=f=220:d=2', '-c:v', 'libx264', '-c:a', 'aac',
                            '-shortest', str(source)], check=True, timeout=15)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:d=1', str(track)],
                           check=True, timeout=15)
            cue = {'pan': -.85, 'distance': 'far', 'space': 'hall', 'level': 'normal'}
            film_score.mix_score(source, output, [(track, .15, .35, 0), (track, 1.1, .45, .1)],
                                 [(track, .7, .5, cue)])
            data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                                      '-show_format', '-of', 'json', str(output)], timeout=10))
            self.assertEqual({stream['codec_type'] for stream in data['streams']}, {'video', 'audio'})
            self.assertAlmostEqual(float(data['format']['duration']), 2, delta=.05)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_score_accent_aligns_real_audio_rise_to_picture_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / 'accent.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:d=8',
                            '-af', 'volume=if(lt(t\\,3)\\,0.04\\,1):eval=frame', str(track)],
                           check=True, timeout=15)
            self.assertAlmostEqual(film_score.accent_offset(track, 1.5, 3), 1.5, delta=.3)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_spatial_effect_is_audibly_left_and_starts_before_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, track, output = (root / name for name in ('source.mp4', 'fx.wav', 'mixed.mp4'))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=128x72:d=2',
                            '-f', 'lavfi', '-i', 'anullsrc=cl=stereo:r=48000', '-c:v', 'libx264', '-c:a', 'aac',
                            '-shortest', str(source)], check=True, timeout=15)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:d=1', str(track)],
                           check=True, timeout=15)
            cue = {'shot_id': 'second', 'source': '金属声', 'offset': -.4, 'duration': .7, 'pan': -.9,
                   'distance': 'far', 'space': 'hall', 'level': 'normal'}
            timeline = film_score.sound_timeline([{'sound_effects': [cue]}], [{'id': 'first'}, {'id': 'second'}],
                                                 [(0, 1), (0, 1)], {('金属声', .7): track})
            film_score.mix_score(source, output, [], timeline)
            pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.7', '-i', str(output),
                                           '-t', '0.2', '-map', '0:a:0', '-ac', '2', '-f', 's16le', '-'], timeout=10)
            samples = array('h', pcm)
            left, right = (sum(abs(value) for value in samples[channel::2]) for channel in range(2))
            self.assertGreater(left, 10_000)
            self.assertGreater(left, 2 * right)

    def test_feature_anchors_preserve_exact_text_at_original_offsets(self):
        source = '第一章\n\n' + '猫城的灯在风里摇。' * 43 + '\n\n第二章\n\n门外响起脚步。'
        anchors = novel_film.feature_anchors(novel_film.source_chunks(source))
        self.assertEqual([item['offset'] for item in anchors], sorted({item['offset'] for item in anchors}))
        for item in anchors:
            quote, start = item['quote'], item['offset']
            self.assertLessEqual(len(quote), 160)
            self.assertEqual(source[start:start + len(quote)], quote)
        self.assertEqual(''.join(item['quote'] for item in anchors), source.replace('\n', ''))

    def test_short_plan_repairs_unknown_asset_before_publishing(self):
        excerpt = '船靠岸。守门人转身举灯，照见门闩上有一枚陌生的铜扣。'
        raw = {field: '港口' for field in ('title', 'coverage_note', 'hook', 'ending',
                                          'visual_style', 'visual_medium', 'color_script', 'continuity_rules', 'concept')}
        raw.update(pilot_shot=1, assets=[{'id': 'ref_1', 'name': '港口', 'kind': 'location',
                                         'design_note': '石门', 'signature': '半圆石门',
                                         'material_logic': '湿石受侧光', 'image_prompt': '夜色下的石门'}])
        raw['shots'] = []
        for index, scale in enumerate(('wide', 'medium', 'close') * 3):
            shot = {field: f'{field} {index}' for field in ('beat', 'first_frame', 'last_frame',
                    'action', 'camera', 'timing', 'lighting', 'sound', 'continuity', 'transition', 'quality_check')}
            shot.update(source_excerpt=excerpt, reference_ids=['ref_1'], dialogue='', scale=scale, clip_seconds=6)
            shot.update(grade='natural', grade_reason='进入船舱', color_design='墨黑底，朱红只落在门闩', composition='前景石门')
            shot.update(screen_action=f'举灯照向第 {index} 处', handoff='用反打揭示铜扣', spectacle=None)
            raw['shots'].append(shot)
        invalid = copy.deepcopy(raw)
        invalid['shots'][4]['reference_ids'] = ['missing']
        audio = {'cues': [{'shot': index, 'start': .5, 'end': 4.5, 'text': '潮水回来了。',
                           'delivery': '慢读', 'sound_bridge': '风声延续'} for index in range(1, 6)],
                 'music': [{'first': 2, 'last': 4, 'track': 'classical', 'reason': '进入船舱'}],
                 'effects': [{'shot': 2, 'offset': -.7, 'duration': 1, 'source': '潮声拍在石门后', 'pan': -.75,
                              'space': 'hall', 'distance': 'far', 'level': 'subtle'}]}
        with patch.object(novel_film, 'choose_passage', return_value={'excerpt': excerpt}), \
             patch.object(novel_film, 'call_json', side_effect=[invalid, raw, audio]) as call:
            result = novel_film.generate({}, excerpt, '港口', 'scene', {}, lambda *args: None)
        self.assertIn('第 5 镜', call.call_args_list[1].args[1]['error'])
        self.assertEqual(result['film']['pilot_shot_id'], 'novel-film-shot-01')
        self.assertEqual(sum(shot['clip_seconds'] for shot in result['scenes'][0]['shots']), 54)
        self.assertEqual(result['film']['narration']['cues'][2]['timeline_start'], 12.5)
        self.assertEqual(result['film']['music_credits'][0]['license'], 'CC BY 4.0')
        first = result['scenes'][0]['shots'][0]
        second, third = result['scenes'][0]['shots'][1:3]
        self.assertIn('朱红只落在门闩', first['video_prompt'])
        self.assertIn('造型媒介', first['video_prompt'])
        self.assertIn('请从本镜原生音轨中排除：潮声拍在石门后', first['video_prompt'])
        self.assertEqual(second['post_sound_sources'], first['post_sound_sources'])
        self.assertEqual(third['post_sound_sources'], [])
        h3_prompt = film_render.take_prompt(first, {'ref_1': result['bible']['assets'][0]})
        self.assertIn('结束画面：last_frame 0', h3_prompt)
        self.assertIn('固定人物、道具与场景状态：continuity 0', h3_prompt)
        self.assertIn('<Picture 1> 港口，固定空间地标、布局和光源关系。', h3_prompt)
        self.assertEqual(h3_prompt.count(first['image_prompt']), 1)
        self.assertIn('以这一版开场构图为准：从半圆石门向外看',
                      film_render.take_prompt({**first, 'image_prompt': '从半圆石门向外看'},
                                              {'ref_1': result['bible']['assets'][0]}))
        sound = film_score.sound_timeline(result['scenes'], result['scenes'][0]['shots'], [(0, 6)] * 9,
                                          {('潮声拍在石门后', 1): Path('tide.wav')})
        self.assertAlmostEqual(sound[0][1], 5.3)
        changed = video_prompts.edit_prompts(result, {first['id']: {'screen_action': '只拍水面灯影',
                                                                  'narration_text': '水涨起来了。'}})
        self.assertIn('只拍水面灯影', changed['scenes'][0]['shots'][0]['video_prompt'])
        self.assertEqual(changed['scenes'][0]['shots'][0]['source_excerpt'], first['source_excerpt'])
        self.assertEqual(changed['film']['narration']['cues'][0]['text'], '水涨起来了。')
        with self.assertRaisesRegex(ValueError, '录制时间'):
            video_prompts.edit_prompts(result, {first['id']: {'narration_text': '水' * 30}})

    def test_trailer_reads_whole_input_binds_nonchronological_sources_and_repairs_review(self):
        source = ('第一章\n\n  渡客在黑暗中登船。\n\n第二章\n\n港口的灯一盏接一盏亮了。'
                  '\n\n第三章\n\n钟响之前，渡客抓住了最后一根绳。')
        cues = ('hook', 'world', 'world', 'pressure', 'pressure', 'breath', 'cliffhanger')
        moments = ('moment_03_2', 'moment_01_2', 'moment_01_2', 'moment_02_2', 'moment_02_2',
                   'moment_03_2', 'moment_03_2')
        plan = {field: '夜港' for field in ('title', 'coverage_note', 'hook', 'ending',
                                          'visual_style', 'visual_medium', 'color_script', 'continuity_rules', 'concept')}
        plan.update(pilot_shot=2, audience_question='渡客能登岸吗？', withheld='渡客最终没能登岸',
                    assets=[{'id': 'loc', 'name': '港口', 'kind': 'location',
                                         'design_note': '河边', 'signature': '一排高低灯',
                                         'material_logic': '湿木板的反射', 'image_prompt': '晚灯'}], shots=[])
        for index, (role, moment) in enumerate(zip(cues, moments, strict=True)):
            shot = {key: f'{key} {index}' for key in ('beat', 'first_frame', 'last_frame', 'action', 'camera',
                    'timing', 'lighting', 'sound', 'continuity', 'transition', 'quality_check', 'grade_reason',
                    'composition', 'color_design', 'screen_action', 'handoff')}
            shot.update(trailer_beat=role, moment_id=moment, reference_ids=['loc'], dialogue='',
                        grade='vivid' if index < 3 else 'monochrome',
                        scale=('wide', 'medium', 'close')[index % 3], clip_seconds=6 if index == 3 else 7,
                        spectacle=None)
            if index == 1:
                shot['spectacle'] = {'setup': '桥边站着渡客', 'reveal': '整排倒影升起',
                                     'payoff': '渡客抬头', 'protected': '渡客面孔与石桥'}
            plan['shots'].append(shot)
        reviewed = {'approved': False, 'note': '第七镜提早揭示结局'}
        analysis = {'summary': '渡客回到港口', 'moments': [{'anchor': 2, 'action': '有一处行动', 'appeal': '悬念'}]}
        with patch.object(novel_film, 'call_json', side_effect=[analysis, analysis, analysis, plan, reviewed,
                          plan, {'approved': True}]) as model, \
             patch.object(novel_film, 'write_narration', return_value={'cues': []}) as narration:
            def write_audio(_title, _kind, _bible, scenes, *_args):
                scenes[0]['music'] = []
                return {'cues': []}
            narration.side_effect = write_audio
            package = novel_film.generate({}, source, '夜港', 'trailer', {}, lambda *args: None)
        for i, sentence in enumerate(('渡客在黑暗中登船。', '港口的灯一盏接一盏亮了。', '钟响之前，渡客抓住了最后一根绳。')):
            self.assertIn(sentence, [text.strip() for _, text in model.call_args_list[i].args[1]['passages']])
        self.assertIn('第七镜提早揭示结局', model.call_args_list[5].args[1]['error'])
        shots = package['scenes'][0]['shots']
        self.assertEqual([shot['moment_id'] for shot in shots], list(moments))
        self.assertEqual(shots[0]['source_excerpt'], '钟响之前，渡客抓住了最后一根绳。')
        self.assertIn('整排倒影升起', shots[1]['video_prompt'])
        for moment in package['film']['trailer_moments'].values():
            self.assertEqual(moment['excerpt'], source[moment['start_char']:moment['end_char']])
        with self.assertRaisesRegex(ValueError, '原著前、中、后'):
            for shot in shots:
                shot['moment_id'] = moments[0]
            novel_film.bind_trailer_shots({'shots': shots}, package['film']['trailer_moments'])

    def test_gateway_retries_interrupted_stream_without_mixing_responses(self):
        def handler(request):
            traces.append(request.headers['x-trace-id'])
            if len(traces) == 1:
                return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"bad"}}]}\n')
            result = {'choices': [{'delta': {'content': '{"title":"港口"}'}, 'finish_reason': 'stop'}]}
            return httpx.Response(200, text=f'data:{json.dumps(result)}\ndata: [DONE]\n')

        traces = []
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key'
            key_file.write_text('test-key')
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with patch.object(local_story_gateway, 'KEY_FILE', key_file), \
                     patch.object(local_story_gateway, 'URL', 'https://story.example.test/v1/chat/completions'), \
                     patch.object(local_story_gateway, 'enabled', return_value=True), \
                     patch.object(local_story_gateway.httpx, 'stream', side_effect=lambda *args, **kwargs:
                                  client.stream(*args, **{k: v for k, v in kwargs.items() if k != 'trust_env'})):
                    self.assertEqual(local_story_gateway.generate_json('test', 100), {'title': '港口'})
        self.assertEqual(len(set(traces)), 2)

    def test_missing_comfy_receipt_is_distinguished_from_pending(self):
        with patch.object(rented_h3, 'session') as session, patch.object(rented_h3, 'history') as history:
            queue = session.return_value.__enter__.return_value.get.return_value
            queue.status_code = 200
            history.return_value = None
            queue.json.return_value = {'queue_pending': [[0, 'job-1']]}
            self.assertEqual(rented_h3.poll('http://localhost', 'job-1'), 'queued')
            queue.json.return_value = {'queue_pending': []}
            self.assertEqual(rented_h3.poll('http://localhost', 'job-1'), 'missing')
            history.side_effect = [None, {'status': {'completed': True, 'status_str': 'success'}}]
            self.assertEqual(rented_h3.poll('http://localhost', 'job-1'), 'completed')

    def test_resume_keeps_approved_cut_and_later_remote_receipt(self):
        shots = [{'id': name, 'reference_ids': ['loc'], 'video_prompt': name} for name in ('one', 'two')]
        asset = {'id': 'loc', 'name': '港口', 'kind': 'location'}
        job = {'id': 'test-film', 'film_snapshot': {'scenes': [{'shots': shots}],
               'bible': {'assets': [asset], 'visual_style': '夜景'}, 'film': {'kind': 'trailer'},
               'options': {'aspect_ratio': '16:9'}},
               'phase': 'film', 'ratio': '16:9', 'config': {'provider': 'minimax'},
               'accepted_art': ['loc'], 'reviews': [], 'selections': {'one': {
                   'file': 'film-01-1.mp4', 'in_seconds': 0, 'out_seconds': 6, 'previous_file': None}},
               'active_take': {'shot_id': 'two', 'file': 'film-02-1.mp4', 'status': 'queued',
                               'provider_task_id': 'remote-2'}}

        def receive(current, shot, path, *args):
            self.assertEqual(shot['id'], 'two')
            self.assertEqual(current['active_take']['provider_task_id'], 'remote-2')
            path.touch()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / job['id']
            work.mkdir()
            (work / 'film-01-1.mp4').touch()
            with patch.object(film_render.workflow, 'ROOT', root), \
                 patch.object(film_render.workflow, 'put'), \
                 patch.object(film_render, 'reference_library', return_value=work), \
                 patch.object(film_render, 'art', return_value=work / 'art.jpg'), \
                 patch.object(film_render, 'image_data', return_value='reference'), \
                 patch.object(film_render, 'outgoing_frame', return_value='last frame'), \
                 patch.object(film_render, 'render_take', side_effect=receive), \
                 patch.object(film_render, 'review', return_value={'accepted': True, 'score': 90,
                              'in_seconds': 0, 'out_seconds': 6, 'note': '通过'}) as review, \
                 patch.object(film_render.workflow, 'assemble_paths',
                              side_effect=lambda paths, output, *args, **kwargs: output.touch()), \
                 patch.object(film_render.openfilm_export, 'generate_plan', return_value={}), \
                 patch.object(film_render.openfilm_export, 'make_project', return_value=(work / 'openfilm', 12)), \
                 patch.object(film_render.openfilm_export, 'render',
                              side_effect=lambda project, output, duration, **kwargs: output.touch()):
                film_render.produce(job, {'video': 'key', 'judge': 'key'})
            review.assert_called_once()
            self.assertEqual(review.call_args.args[1]['id'], 'two')
            self.assertEqual(job['selections']['two']['previous_file'], 'film-01-1.mp4')
            self.assertTrue((root / 'test-film.mp4').is_file())
