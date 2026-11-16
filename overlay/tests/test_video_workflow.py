import copy
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infinite_canvas'/'backend'))
from flask import Flask,g
import video_prompts as prompts
import video_providers as providers
import video_workflow as workflow


class VideoPromptTests(unittest.TestCase):
    def setUp(self):
        self.payload={'scenes':[{'id':'s1','title':'渡口','script_text':'老周放下船票。林岚说：等一下。'}], 'workspace_meta':{'sceneweave':{'boundary':'不得改变结局'}}}
        self.bible={'visual_style':'冷灰雨夜','continuity_rules':'船票保持湿润','assets':[{'id':'ref_1','name':'老周','kind':'character','design_note':'服装为制作设计','image_prompt':'老周穿雨衣'}]}
        self.shot={k:'具体内容' for k in ('beat','source_excerpt','first_frame','last_frame','action','camera','timing','lighting','sound','continuity','transition','quality_check')}
        self.shot.update(source_excerpt='老周放下船票。',dialogue='林岚：等一下。',reference_ids=['ref_1'])

    def make(self):
        with patch.object(prompts,'generate_text',side_effect=[json.dumps(self.bible),json.dumps({'coverage_note':'保留船票与等待','shots':[self.shot]})]) as mock:
            result=prompts.generate(self.payload,{'style':'custom','custom_style':'黑白手持'})
            self.assertIn('不得改变结局',mock.call_args.args[0])
            self.assertEqual(mock.call_args.kwargs['model'],'qwen3.8-flash')
            return result

    def test_complete_saved_schema_and_edits(self):
        result=self.make()
        self.assertFalse(result['media_generated'])
        self.assertEqual(result['scenes'][0]['shots'][0]['id'],'s1-shot-01')
        edited=prompts.edit_prompts(result,{'s1-shot-01':{'video_prompt':'新的运动描述'}})
        self.assertNotEqual(result,edited)
        self.assertEqual(result['source_hash'],edited['source_hash'])
        with self.assertRaises(ValueError):prompts.edit_prompts(result,{'s1-shot-01':{'source_excerpt':'捏造'}})

    def test_reject_wrong_source_reference_and_options(self):
        for changed in [dict(source_excerpt='原文不存在'),dict(reference_ids=['fake'])]:
            with self.assertRaises(ValueError):prompts.validate_shots({'shots':[{**self.shot,**changed}]},self.payload['scenes'][0],prompts.options({}),{'ref_1'})
        for opts in [{'model':'other'},{'style':'custom'},{'scope':'selected'},{'duration':30}]:
            with self.assertRaises(ValueError):prompts.options(opts)

    def test_missing_scene_script_does_not_call_model(self):
        with patch.object(prompts,'generate_text') as mock:
            with self.assertRaises(ValueError):prompts.generate({'scenes':[{'id':'s1'}]}, {})
            mock.assert_not_called()

    def test_runway_motion_prompt_separates_audio_and_appearance(self):
        s={**self.shot,'action':'转身','camera':'缓慢推近','timing':'前两秒停顿'}
        result=prompts.compile_prompt(s,{**prompts.options({}),'adapter':'runway'},self.bible)
        self.assertIn('转身',result);self.assertNotIn('冷灰雨夜',result);self.assertNotIn('林岚',result)


class ProviderTests(unittest.TestCase):
    def test_all_provider_bodies_and_modes(self):
        for provider in providers.PROVIDERS:
            for model in provider['models']:
                for image in (None,'data:image/png;base64,YQ=='):
                    config={'provider':provider['id'],'model':model,'workspace_id':'workspace-1','region':'cn-beijing'}
                    url,headers,body,poll=providers.build_request(config,'人物向右转身',6,'9:16',image)
                    self.assertTrue(url.startswith('https://'));self.assertNotIn('Authorization',headers)
                    self.assertNotIn('api_key',json.dumps(body))
                    if provider['id']=='wan':self.assertEqual(body['parameters']['ratio'],'adaptive' if image else '9:16')
                    if provider['id']=='ark' and image:self.assertEqual(body['content'][1]['role'],'first_frame')
                    if provider['id']=='runway':self.assertTrue(url.endswith('image_to_video' if image else 'text_to_video'))

    def test_poll_success_and_failure_each_provider(self):
        cases=[('wan',{'output':{'task_status':'SUCCEEDED','video_url':'https://example/video'}},'completed'),('ark',{'status':'failed','error':{'message':'quota'}},'failed'),('runway',{'status':'SUCCEEDED','output':['https://example/video']},'completed')]
        for provider,response,status in cases:
            with patch.object(providers,'request_json',return_value=response):
                out=providers.poll({'config':{'provider':provider},'poll_url':'https://official/tasks/1'},'test-key')
                self.assertEqual(out['status'],status)

    def test_invalid_model_and_missing_key_never_network(self):
        with patch.object(providers,'request_json') as mock:
            with self.assertRaises(ValueError):providers.submit({'provider':'wan'},'x',6,'16:9',None,'')
            with self.assertRaises(ValueError):providers.build_request({'provider':'runway','model':'gen4.5'},'字'*1001,6,'16:9')
            mock.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=patch.object(workflow,'ROOT',Path(self.tmp.name));self.root.start()
        self.app=Flask(__name__)
        @self.app.before_request
        def auth():g.current_user={'id':'u1'};g.current_project={'id':'p1'}
        self.package={'status':'completed','created_at':'now','source_hash':'hash','options':{'mode':'text','aspect_ratio':'16:9'},'scenes':[{'shots':[{'id':'s1-shot-01','video_prompt':'镜头推近','duration_seconds':6}]}]}
        workflow.register(self.app,lambda:{'workspace_meta':{'video_package':self.package}})
        self.client=self.app.test_client()
        self.request={'request_id':str(uuid.uuid4()),'shot_id':'s1-shot-01','package_created_at':'now','config':{'provider':'runway','model':'gen4.5'}}
    def tearDown(self):self.root.stop();self.tmp.cleanup()

    def test_default_empty_key_no_billable_request(self):
        with patch.object(workflow,'fingerprint',return_value='hash'),patch.object(providers,'submit') as mock:
            r=self.client.post('/video-workflow/jobs',json=self.request)
            self.assertEqual(r.status_code,422);mock.assert_not_called()

    def test_submission_idempotency_and_secret_not_stored(self):
        with patch.object(workflow,'fingerprint',return_value='hash'),patch.object(providers,'submit',return_value={'provider_task_id':'remote1','poll_url':'https://official/tasks/remote1'}) as mock:
            for _ in range(2):
                r=self.client.post('/video-workflow/jobs',json=self.request,headers={'X-Video-Api-Key':'video-secret'})
                self.assertEqual(r.status_code,200);self.assertNotIn('key_hash',r.json)
            mock.assert_called_once()
            raw=(Path(self.tmp.name)/'jobs.sqlite3').read_bytes()
            self.assertNotIn(b'video-secret',raw)
        with self.app.test_request_context():
            g.current_user={'id':'another'};g.current_project={'id':'p1'}
            with self.assertRaises(ValueError):workflow.get(self.request['request_id'])

    def test_unknown_submission_never_automatically_retried(self):
        with patch.object(workflow,'fingerprint',return_value='hash'),patch.object(providers,'submit',side_effect=RuntimeError('timeout')) as mock:
            for _ in range(2):
                r=self.client.post('/video-workflow/jobs',json=self.request,headers={'X-Video-Api-Key':'video-secret'})
                self.assertEqual(r.json['status'],'submission_unknown')
            mock.assert_called_once()

    def test_duplicate_shot_and_recovery_without_new_submission(self):
        with patch.object(workflow,'fingerprint',return_value='hash'),patch.object(providers,'submit',side_effect=RuntimeError('timeout')) as mock:
            self.client.post('/video-workflow/jobs',json=self.request,headers={'X-Video-Api-Key':'video-secret'})
            duplicate={**self.request,'request_id':str(uuid.uuid4())}
            self.assertEqual(self.client.post('/video-workflow/jobs',json=duplicate,headers={'X-Video-Api-Key':'video-secret'}).status_code,422)
            with patch.object(providers,'poll',return_value={'status':'running','output_urls':[],'error':''}):
                r=self.client.post('/video-workflow/jobs/'+self.request['request_id']+'/recover',json={'task_id':'remote-123'},headers={'X-Video-Api-Key':'video-secret'})
                self.assertEqual(r.status_code,200);self.assertEqual(r.json['status'],'running')
            mock.assert_called_once()

    def test_explicit_provider_rejection_is_failed(self):
        with patch.object(workflow,'fingerprint',return_value='hash'),patch.object(providers,'submit',side_effect=providers.RejectedRequest('quota')):
            r=self.client.post('/video-workflow/jobs',json=self.request,headers={'X-Video-Api-Key':'video-secret'})
            self.assertEqual(r.json['status'],'failed')

    def test_stale_package_and_private_download_rejected(self):
        with patch.object(workflow,'fingerprint',return_value='changed'):
            self.assertEqual(self.client.post('/video-workflow/jobs',json=self.request).status_code,422)
        for url in ['http://localhost/a','https://127.0.0.1/a','https://example.com/a','file:///etc/passwd']:
            with self.assertRaises(ValueError):workflow.safe_media_url(url)


if __name__=='__main__':unittest.main()
