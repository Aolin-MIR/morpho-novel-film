"""Explicit official video API adapters. Keys are supplied per request, never persisted."""
import re
from urllib.parse import quote
import requests

PROVIDERS = [
    {'id': 'minimax', 'label': 'MiniMax H3', 'models': ['MiniMax-H3'],
     'doc': 'https://platform.minimax.io/docs/api-reference/video-generation-v2-create'},
    {'id':'wan','label':'阿里云万相 · 官方 API','models':['wan3.0-video','wan3.0-video-prime'], 'doc':'https://help.aliyun.com/en/model-studio/wan3-video-generation-api-reference'},
    {'id':'ark','label':'火山引擎 Seedance · 官方 API','models':['doubao-seedance-2-0-260128','doubao-seedance-2-0-fast-260128'], 'doc':'https://docs.volcengine.com/docs/ark/create-video-generation-task-api'},
    {'id':'runway','label':'Runway · 官方 API（含 Veo / 可灵）','models':['gen4.5','veo3.1','veo3.1_fast','kling3.0_pro'], 'doc':'https://docs.dev.runwayml.com/api/'},
]
REGIONS = ['cn-beijing','ap-southeast-1','ap-northeast-1','eu-central-1','us-east-1','cn-hongkong']

class RejectedRequest(ValueError):
    """A definite provider rejection; unlike a timeout, no task was accepted."""


def build_request(config, prompt, duration, ratio, first_frame=None, references=()):
    if not isinstance(config, dict):
        raise ValueError('请选择视频服务。')
    provider, model = config.get('provider'), config.get('model')
    spec = next((p for p in PROVIDERS if p['id']==provider), None)
    if not spec or model not in spec['models']:
        raise ValueError('不支持的视频服务或模型，请重新选择。')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>5000:
        raise ValueError('视频提示词必须为 1–5000 字。')
    if provider == 'minimax':
        if type(duration) is not int or not 4 <= duration <= 15 or ratio not in {'16:9', '9:16'}:
            raise ValueError('H3 镜头需要 4–15 秒与横屏或竖屏画幅。')
        if (first_frame and references) or len(references) > 9:
            raise ValueError('H3 首帧与参考图需分别使用，参考图最多 9 张。')
        content = [{'type': 'text', 'text': prompt}]
        if first_frame:
            content.append({'type': 'image_url', 'role': 'first_frame', 'url': first_frame})
        content.extend({'type': 'image_url', 'role': 'reference_image', 'url': ref} for ref in references)
        base = 'https://api.minimax.io/v2'
        body = {'model': model, 'content': content, 'duration': duration,
                'ratio': 'adaptive' if first_frame else ratio, 'resolution': '2K'}
        return base + '/video_generation', {'Content-Type': 'application/json'}, body, base + '/query/video_generation/'
    if references:
        raise ValueError('本流程的多图参考仅支持 MiniMax H3。')
    if type(duration) is not int or duration not in {4,6,8} or ratio not in {'16:9','9:16'}:
        raise ValueError('请选择 4、6、8 秒与横屏或竖屏画幅。')
    if first_frame and (not isinstance(first_frame,str) or not first_frame.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,'))):
        raise ValueError('请上传有效首帧图片。')
    headers = {'Content-Type':'application/json'}
    if provider=='wan':
        workspace = config.get('workspace_id','')
        region = config.get('region','cn-beijing')
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',workspace) or region not in REGIONS:
            raise ValueError('万相 3.0 需要有效的 Workspace ID 和地域。')
        base=f'https://{workspace}.{region}.maas.aliyuncs.com/api/v1'
        body={'model':model,'input':{'prompt':prompt},'parameters':{'duration':duration,'ratio':ratio,'resolution':'720P','audio':True}}
        if first_frame:
            body['input']['media']=[{'type':'first_frame','url':first_frame}]
            body['parameters']['ratio']='adaptive'
        headers['X-DashScope-Async']='enable'
        return base+'/services/aigc/video-generation/video-synthesis',headers,body,base+'/tasks/'
    if provider=='ark':
        base='https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks'
        body={'model':model,'content':[{'type':'text','text':prompt}],'duration':duration,'ratio':ratio,'resolution':'720p','generate_audio':True}
        if first_frame:body['content'].append({'type':'image_url','image_url':{'url':first_frame},'role':'first_frame'})
        return base,headers,body,base+'/'
    if len(prompt.encode('utf-16-le'))//2>1000:
        raise ValueError('Runway 提示词超过 1000 个 UTF-16 字符，请先精简本镜提示词。')
    base='https://api.dev.runwayml.com/v1'
    body={'model':model,'promptText':prompt,'duration':duration,'ratio':('1920:1080' if ratio=='16:9' else '1080:1920') if model=='kling3.0_pro' else ('1280:720' if ratio=='16:9' else '720:1280')}
    if first_frame:body['promptImage']=first_frame
    if model!='gen4.5':body['audio']=True
    headers['X-Runway-Version']='2024-11-06'
    return base+('/image_to_video' if first_frame else '/text_to_video'),headers,body,base+'/tasks/'


def request_json(method,url,headers,body=None, *, read_timeout=60):
    try:
        response=requests.request(method,url,headers=headers,json=body,timeout=(15,read_timeout),allow_redirects=False)
    except requests.RequestException as exc:
        # Do not print request headers or retry billable submissions after an unknown outcome.
        raise RuntimeError('服务连接中断或超时；提交结果可能未知。请到服务商控制台核对，勿重复提交。') from exc
    try:data=response.json()
    except ValueError as exc:raise RuntimeError(f'视频服务返回非 JSON 响应（HTTP {response.status_code}）。') from exc
    if not response.ok:
        code=data.get('code') if isinstance(data, dict) else None
        if not code and isinstance(data, dict) and isinstance(data.get('error'),dict):code=data['error'].get('code')
        error_type=RejectedRequest if 400<=response.status_code<500 else RuntimeError
        raise error_type(f'视频服务拒绝请求（HTTP {response.status_code}，代码 {code or "未提供"}）。请核对权限、余额与参数。')
    if not isinstance(data, dict):
        raise RuntimeError('视频服务返回了无效响应。')
    return data


def submit(config,prompt,duration,ratio,first_frame,key, *, references=()):
    if not isinstance(key,str) or not key.strip():raise ValueError('请填写该视频服务的 API Key。')
    url,headers,body,poll_base=build_request(config,prompt,duration,ratio,first_frame,references)
    headers['Authorization']='Bearer '+key.strip()
    data=request_json('POST',url,headers,body)
    if config['provider'] == 'minimax':
        task_id = data.get('task_id')
    elif config['provider'] == 'wan':
        task_id = (data.get('output') or {}).get('task_id')
    else:
        task_id = data.get('id')
    if not isinstance(task_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}',task_id):
        raise RuntimeError('服务未返回有效任务编号。请核对服务商控制台，勿自动重复提交。')
    return {'provider_task_id':task_id,'poll_url':poll_base+quote(task_id,safe='')}


def poll(job,key):
    if not key:raise ValueError('请重新填写本任务对应服务的 API Key 以查询进度。')
    headers={'Authorization':'Bearer '+key}
    if job['config']['provider']=='runway':headers['X-Runway-Version']='2024-11-06'
    data=request_json('GET',job['poll_url'],headers)
    provider=job['config']['provider']
    if provider == 'minimax':
        task = data.get('task')
        if not isinstance(task, dict) or task.get('id') != job['provider_task_id'] or task.get('model') != job['config']['model']:
            raise RuntimeError('H3 返回的任务或模型与提交记录不一致。')
        status = task.get('status')
        url = (task.get('content') or {}).get('url')
        urls = [url] if isinstance(url, str) and url else []
        error = (task.get('error') or {}).get('message', '')
    elif provider=='wan':
        result=data.get('output') or {};status=result.get('task_status','');urls=[result['video_url']] if result.get('video_url') else [];error=result.get('message') or data.get('message') or ''
    elif provider=='ark':
        status=data.get('status','');result=data.get('content') or {};urls=[result['video_url']] if result.get('video_url') else [];error=(data.get('error') or {}).get('message','')
    else:
        status=data.get('status','');urls=data.get('output') or [];error=data.get('failure') or data.get('failureCode') or ''
    normalized={'pending':'queued','queued':'queued','throttled':'queued','running':'running','succeeded':'completed','failed':'failed','canceled':'canceled','cancelled':'canceled','unknown':'expired'}.get(str(status).lower())
    if not normalized:raise RuntimeError(f'视频服务返回未知任务状态：{status}。')
    if normalized=='completed' and (not isinstance(urls,list) or not urls):raise RuntimeError('任务报告完成，但没有返回视频地址。')
    return {'status':normalized,'output_urls':urls,'error':str(error)[:1000]}
