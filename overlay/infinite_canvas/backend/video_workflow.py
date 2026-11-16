"""In-app video workflow using Flask, SQLite, Requests and FFmpeg."""
import base64
import hashlib
import io
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
import uuid
from urllib.parse import urlparse, urljoin

import requests
from flask import Blueprint, g, jsonify, request, send_file
from PIL import Image
import video_providers as providers
from device_impact import fingerprint

ROOT = Path(os.environ.get('MORPHO_VIDEO_DIR', Path(__file__).parent / 'var' / 'video-workflow'))
bp = Blueprint('video_workflow', __name__, url_prefix='/video-workflow')
workspace_reader = None


def connection():
    ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ROOT/'jobs.sqlite3',timeout=30)
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL)')
    return db


def identity():
    user=getattr(g,'current_user',None);project=getattr(g,'current_project',None)
    if not user or not project:raise ValueError('请先进入项目。')
    return user['id'],project['id']


def put(job, insert=False):
    user,project=identity()
    with connection() as db:
        if insert:
            db.execute('BEGIN IMMEDIATE')
            if job.get('kind') == 'film':
                rows = db.execute('SELECT body FROM jobs WHERE user_id=? AND project_id=?',
                                  (user, project)).fetchall()
                for row in rows:
                    previous = json.loads(row['body'])
                    if previous.get('kind') == 'film' and previous['status'] in {
                        'queued', 'running', 'submission_unknown', 'art_ready', 'pilot_ready'
                    }:
                        raise ValueError('此项目已有短片正在制作或等待核对。')
            elif job.get('kind') != 'assembly':
                rows=db.execute('SELECT body FROM jobs WHERE user_id=? AND project_id=?',(user,project)).fetchall()
                for row in rows:
                    previous=json.loads(row['body'])
                    if previous.get('shot_id')==job.get('shot_id') and previous['status'] in {'submitting','submission_unknown','queued','running'}:
                        raise ValueError('此镜头已有待处理任务，请先查询或核对已有任务。')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(job['id'],user,project,json.dumps(job,ensure_ascii=False),time.time()))
        else:db.execute('UPDATE jobs SET body=?,updated=? WHERE id=? AND user_id=? AND project_id=?',(json.dumps(job,ensure_ascii=False),time.time(),job['id'],user,project))


def get(job_id):
    user,project=identity()
    with connection() as db:
        row=db.execute('SELECT body FROM jobs WHERE id=? AND user_id=? AND project_id=?',(job_id,user,project)).fetchone()
    if not row:raise ValueError('任务不存在或不属于当前项目。')
    return json.loads(row['body'])


def public(job):
    return {k:v for k,v in job.items() if k not in {'poll_url','key_hash','film_snapshot','selections'}}


@bp.before_request
def require_project():
    g.skip_workspace_autosave=True
    if not getattr(g,'current_user',None) or not getattr(g,'current_project',None):
        return jsonify({'error':'请先进入项目。'}),401


@bp.errorhandler(ValueError)
@bp.errorhandler(RuntimeError)
def failure(exc):
    return jsonify({'error':str(exc)}),422


@bp.get('/rental-status')
def rental_status():
    import rented_h3

    endpoint = rented_h3.endpoint()
    user, project = identity()
    with connection() as db:
        rows = db.execute('SELECT body FROM jobs WHERE user_id=? AND project_id=?',
                          (user, project)).fetchall()
    active = [job for row in rows if (job := json.loads(row['body'])).get('config', {}).get('provider') == 'rented_h3'
              and job['status'] in {'queued', 'running', 'submission_unknown'}
              and job.get('phase') != 'art']
    return jsonify({'configured': bool(endpoint), 'ready': bool(endpoint and rented_h3.ready(endpoint)),
                    'active': len(active),
                    'pending_downloads': sum(bool(job.get('active_take')) for job in active)})


@bp.get('/jobs')
def jobs():
    user,project=identity()
    with connection() as db:rows=db.execute('SELECT body FROM jobs WHERE user_id=? AND project_id=? ORDER BY updated DESC LIMIT 200',(user,project)).fetchall()
    return jsonify({'jobs':[public(json.loads(r['body'])) for r in rows], 'providers':providers.PROVIDERS,
                    'regions':providers.REGIONS, 'rental_configured': bool(__import__('rented_h3').endpoint()),'can_assemble':bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))})


def frame_value(value):
    if not value:return None
    if not isinstance(value,str) or len(value)>7_000_000:raise ValueError('首帧图片必须小于 5 MB。')
    try:
        prefix,encoded=value.split(',',1)
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>5_000_000:raise ValueError('首帧图片必须小于 5 MB。')
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in {'PNG','JPEG','WEBP'} or min(img.size)<240 or max(img.size)>8000:
                raise ValueError('首帧需要 PNG、JPEG 或 WebP，每边 240–8000 像素。')
            mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[img.format]
            if prefix!='data:'+mime+';base64':raise ValueError('图片格式与文件内容不符。')
            img.verify()
    except (ValueError, OSError) as exc:raise ValueError('首帧图片无效，请重新选择。') from exc
    return value


def current_package(data):
    workspace = workspace_reader()
    package = (workspace.get('workspace_meta') or {}).get('video_package')
    if not package or package.get('status') != 'completed' or package['source_hash'] != fingerprint(workspace):
        raise ValueError('请先生成与当前剧本一致的视频制作包。')
    if data.get('package_created_at') != package['created_at'] or data.get('package_edited_at') != package.get('edited_at'):
        raise ValueError('制作方案已更新，请重新打开制作台。')
    return package


def claim_film_phase(job):
    user, project = identity()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM jobs WHERE id=? AND user_id=? AND project_id=?',
                         (job['id'], user, project)).fetchone()
        expected = job.pop('expected_status')
        if not row or json.loads(row['body'])['status'] != expected:
            raise ValueError('制作进度已更新，请刷新。')
        if job['status'] == 'queued':
            other_rows = db.execute('SELECT body FROM jobs WHERE user_id=? AND project_id=? AND id<>?',
                                    (user, project, job['id'])).fetchall()
            for other_row in other_rows:
                other = json.loads(other_row['body'])
                if other.get('kind') == 'film' and other['status'] in {
                    'queued', 'running', 'submission_unknown', 'art_ready', 'pilot_ready'
                }:
                    raise ValueError('请先结束项目中正在制作的另一部短片。')
        db.execute('UPDATE jobs SET body=?,updated=? WHERE id=? AND user_id=? AND project_id=?',
                   (json.dumps(job, ensure_ascii=False), time.time(), job['id'], user, project))


def film_keys():
    from byok import current_credential
    credential = current_credential() or {}
    return (request.headers.get('X-Video-Api-Key', '').strip(),
            credential.get('qwen_key') or (credential.get('key') if credential.get('provider') == 'qwen' else ''),
            credential.get('qwen_workspace_id', ''))


@bp.post('/films')
def create_film():
    import rented_h3
    from film_render import H3, enqueue
    from film_score import TRACKS

    data = request.get_json() or {}
    request_id = str(data.get('request_id') or '')
    try:
        job_id = str(uuid.UUID(request_id))
    except ValueError as exc:
        raise ValueError('短片提交编号无效。') from exc
    try:
        return jsonify(public(get(job_id)))
    except ValueError:
        pass
    package = current_package(data)
    if not package.get('film'):
        raise ValueError('请先从小说生成短片分镜。')
    if not package['film'].get('pilot_shot_id'):
        raise ValueError('请重新生成包含试拍镜头的分镜。')
    selected_music = {cue['track'] for scene in package['scenes'] for cue in scene.get('music', [])}
    for track in sorted(selected_music):
        if not (ROOT / 'music' / f'{track}.mp3').is_file():
            raise ValueError(f'本地音乐库缺少《{TRACKS[track][0]}》。')
    target = data.get('target')
    if target == 'code':
        raise ValueError('代码动画正在重做角色和场景运动，当前不能开始制作。')
    video_key, judge_key, sound_workspace = film_keys()
    if target == 'ssh' and rented_h3.endpoint():
        for count in {len(shot['reference_ids']) for scene in package['scenes'] for shot in scene['shots']}:
            rented_h3.profile(count)
        config = {'provider': 'rented_h3', 'model': rented_h3.MODEL}
    elif target == 'api':
        config = H3
    else:
        raise ValueError('请选择已配置的渲染服务。')
    job = {'id': job_id, 'kind': 'film', 'status': 'queued', 'config': config, 'phase': 'pilot',
           'shot_id': package['scenes'][0]['title'], 'source_hash': package['source_hash'],
           'package_created_at': package['created_at'], 'package_edited_at': package.get('edited_at'),
           'ratio': package['options']['aspect_ratio'], 'created_at': time.time(),
           'stage': '正在等待制作', 'completed_shots': 0, 'reviews': [], 'film_snapshot': package}
    try:
        enqueue(job, video_key, judge_key, sound_workspace=sound_workspace)
    except sqlite3.IntegrityError:
        return jsonify(public(get(job_id)))
    return jsonify(public(job)), 202


@bp.get('/films/<job_id>/preview')
def preview_film(job_id):
    job = get(job_id)
    if job.get('kind') != 'film':
        raise ValueError('短片不存在。')
    pilot_id = job['film_snapshot']['film']['pilot_shot_id']
    pilot = job.get('selections', {}).get(pilot_id)
    if not pilot:
        raise ValueError('试拍尚未完成。')
    path = ROOT / job_id / pilot['file']
    return send_file(path, mimetype='video/mp4', conditional=True)


@bp.get('/films/<job_id>/edit-project')
def film_edit_project(job_id):
    job = get(job_id)
    if job.get('kind') != 'film' or job.get('status') != 'completed' or job.get('edit_project') != 'openfilm-project.zip':
        raise ValueError('剪辑工程尚未完成。')
    path = ROOT / job_id / 'openfilm-project.zip'
    if not path.is_file():
        raise ValueError('本地剪辑工程缺失，请先恢复归档。')
    return send_file(path, mimetype='application/zip', as_attachment=True,
                     download_name='film-edit-project.zip', conditional=True)


@bp.get('/films/<job_id>/art')
def list_film_art(job_id):
    job = get(job_id)
    if job.get('kind') != 'film':
        raise ValueError('短片不存在。')
    return jsonify({'assets': job.get('reference_assets', [])})


@bp.get('/films/<job_id>/art/<name>')
def film_art(job_id, name):
    job = get(job_id)
    if job.get('kind') != 'film' or not any(item['file'] == name for item in job.get('reference_assets', [])):
        raise ValueError('参考图不存在。')
    return send_file(ROOT / job_id / name, mimetype='image/jpeg')


@bp.post('/films/<job_id>/close')
def close_film(job_id):
    job = get(job_id)
    status = job['status']
    if job.get('kind') != 'film' or status not in {'submission_unknown', 'art_ready', 'pilot_ready'}:
        raise ValueError('当前步骤无法结束。')
    if status == 'submission_unknown' and (request.get_json() or {}).get('confirmed') is not True:
        raise ValueError('请先确认云端镜头状态。')
    job.update(status='canceled', expected_status=status, stage='已结束本次制作')
    claim_film_phase(job)
    return jsonify(public(job))


@bp.post('/films/<job_id>/resume')
def resume_film(job_id):
    from film_render import enqueue

    job = get(job_id)
    status = job['status']
    active = job.get('active_take') or {}
    if job.get('kind') != 'film' or status not in {'failed', 'submission_unknown'}:
        raise ValueError('当前步骤无需恢复。')
    if job.get('review_exhausted'):
        raise ValueError('本次制作已用完返修机会，请调整分镜后开始新的制作。')
    if status == 'submission_unknown' and not active.get('provider_task_id'):
        raise ValueError('未收到云端任务编号，请先在云端核对。')
    if job['config']['provider'] == 'openfilm_code':
        raise ValueError('旧版代码动画无法满足角色运动要求，请使用新的制作方案。')
    if status == 'failed':
        job.pop('active_take', None)
    key, judge_key, sound_workspace = film_keys()
    job.update(status='queued', expected_status=status, stage='正在等待恢复', error='')
    enqueue(job, key, judge_key, resume=True, sound_workspace=sound_workspace)
    return jsonify(public(job)), 202


@bp.post('/jobs')
def create_job():
    data=request.get_json() or {}
    job_id=str(data.get('request_id') or '')
    try:uuid.UUID(job_id)
    except ValueError as exc:raise ValueError('缺少有效提交编号。') from exc
    # Repeated requests return the first result, including an ambiguous submission.
    try:return jsonify(public(get(job_id)))
    except ValueError:pass
    package = current_package(data)
    if package['options']['mode'] == 'references':
        raise ValueError('小说短片请使用自动制作。')
    shot=next((s for scene in package['scenes'] for s in scene['shots'] if s['id']==data.get('shot_id')),None)
    if not shot:raise ValueError('镜头不存在。')
    frame=frame_value(data.get('first_frame'))
    if package['options']['mode']=='image' and not frame:raise ValueError('此制作包使用图生视频，请先上传本镜首帧。')
    if package['options']['mode']=='text' and frame:raise ValueError('此制作包使用文生视频，不接受首帧；请切换制作方式后重新生成提示词。')
    config={k:(data.get('config') or {}).get(k,'') for k in ('provider','model','workspace_id','region')}
    key=request.headers.get('X-Video-Api-Key','').strip()
    if not key:raise ValueError('视频 API Key 默认为空，请先填写你自己的密钥。')
    providers.build_request(config,shot['video_prompt'],shot['duration_seconds'],package['options']['aspect_ratio'],frame)
    job={'id':job_id,'status':'submitting','config':config,'shot_id':shot['id'],'source_hash':package['source_hash'],
         'package_created_at':package['created_at'],'prompt':shot['video_prompt'],'duration_seconds':shot['duration_seconds'],
         'ratio':package['options']['aspect_ratio'],'created_at':time.time(),'key_hash':hashlib.sha256(key.encode()).hexdigest()}
    try:put(job,insert=True)
    except sqlite3.IntegrityError:return jsonify(public(get(job_id)))
    try:
        job.update(providers.submit(config,job['prompt'],job['duration_seconds'],job['ratio'],frame,key))
        job['status']='queued'
    except providers.RejectedRequest as exc:
        job['status']='failed';job['error']=str(exc)
    except (ValueError,RuntimeError) as exc:
        job['status']='submission_unknown';job['error']=str(exc)
    put(job)
    return jsonify(public(job))


@bp.post('/jobs/<job_id>/poll')
def poll_job(job_id):
    job=get(job_id)
    if job.get('kind') == 'film' or job['status'] not in {'queued','running'}:
        return jsonify(public(job))
    key=request.headers.get('X-Video-Api-Key','').strip()
    if hashlib.sha256(key.encode()).hexdigest()!=job['key_hash']:raise ValueError('请填写创建本任务时使用的视频密钥。')
    result=providers.poll(job,key)
    result['error']=result.get('error','').replace(key,'[已隐藏]')
    job.update(result);put(job)
    return jsonify(public(job))


@bp.post('/jobs/<job_id>/recover')
def recover_job(job_id):
    job=get(job_id)
    if job.get('kind') == 'film':
        raise ValueError('请在短片制作记录中查看任务。')
    if job['status'] not in {'submitting','submission_unknown'}:raise ValueError('仅待核对的提交需要恢复任务编号。')
    task_id=str((request.json or {}).get('task_id') or '').strip()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}',task_id):raise ValueError('请粘贴服务商返回的任务编号。')
    key=request.headers.get('X-Video-Api-Key','').strip()
    if hashlib.sha256(key.encode()).hexdigest()!=job['key_hash']:raise ValueError('请填写原任务的视频服务密钥。')
    _,_,_,poll_base=providers.build_request(job['config'],job['prompt'],job['duration_seconds'],job['ratio'])
    job.update(provider_task_id=task_id,poll_url=poll_base+task_id)
    result=providers.poll(job,key)
    result['error']=result.get('error','').replace(key,'[已隐藏]')
    job.update(result);put(job)
    return jsonify(public(job))


def safe_media_url(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None,443}:
        raise ValueError('视频下载地址无效。')
    allowed=('aliyuncs.com','volces.com','byteimg.com','runwayml.com','runway.cloud','cloudfront.net','amazonaws.com','googleapis.com','hailuoai.com','minimax.io','minimaxi.com','minimax.chat')
    if not any(parsed.hostname==host or parsed.hostname.endswith('.'+host) for host in allowed):
        raise ValueError('视频地址不在已支持服务的素材域名范围内。')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('视频地址不可访问。')


def download_job(job):
    if job['status'] != 'completed':
        raise ValueError('视频尚未生成成功。')
    target = ROOT / (job['id'] + '.mp4')
    if target.exists():
        return target
    if not job.get('output_urls'):
        raise ValueError('视频素材已丢失。')
    url=job['output_urls'][0]
    for _ in range(4):
        safe_media_url(url)
        response=requests.get(url,timeout=(15,60),stream=True,allow_redirects=False)
        if response.is_redirect:
            url=urljoin(url,response.headers['Location']);response.close();continue
        if not response.ok:response.close();raise ValueError('视频下载失败，服务商链接可能已过期。')
        partial=ROOT/(job['id']+'.part')
        try:
            size=0
            with partial.open('wb') as out:
                for chunk in response.iter_content(1024*1024):
                    size+=len(chunk)
                    if size>300_000_000:raise ValueError('单镜文件超过 300 MB。')
                    out.write(chunk)
            partial.replace(target)
        finally:
            response.close()
            if partial.exists():partial.unlink()
        return target
    raise ValueError('视频下载跳转次数过多。')


@bp.post('/jobs/<job_id>/archive')
def archive_job(job_id):
    job=get(job_id)
    download_job(job)
    job['archived']=True;put(job)
    return jsonify(public(job))


@bp.get('/jobs/<job_id>/file')
def media_file(job_id):
    job=get(job_id)
    path=ROOT/(job_id+'.mp4')
    if not path.exists() or not job.get('archived'):raise ValueError('请先保存视频到项目。')
    return send_file(path,mimetype='video/mp4',conditional=True,as_attachment=request.args.get('download')=='1',download_name=f"{job.get('shot_id','film')}.mp4")


def assemble_paths(paths, output, ratio, *, cuts=None, high_resolution=False, narration=None, grades=None):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('服务器尚未安装 FFmpeg。')
    if grades is not None and (len(grades) != len(paths) or any(grade not in {'natural', 'vivid', 'monochrome'} for grade in grades)):
        raise ValueError('成片调色设置与镜头不匹配。')
    work=ROOT/('assembly-'+str(uuid.uuid4()));work.mkdir()
    long_side, short_side = (2048, 1152) if high_resolution else (1280, 720)
    width, height = (long_side, short_side) if ratio == '16:9' else (short_side, long_side)
    try:
        segments=[]
        for i,path in enumerate(paths):
            probe=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30)
            info=json.loads(probe.stdout)
            if not any(s['codec_type']=='video' for s in info['streams']):raise ValueError('素材没有视频轨道。')
            duration=float(info.get('format',{}).get('duration',0))
            if not 0<duration<=60:raise ValueError('镜头时长无效或超过 60 秒。')
            start = 0
            if cuts is not None:
                start, end = cuts[i]
                if not 0 <= start < end <= duration:
                    raise ValueError('剪辑时间超出镜头范围。')
                duration = end - start
            has_audio=any(s['codec_type']=='audio' for s in info['streams'])
            target=work/f'{i:04d}.mp4'
            command=['ffmpeg','-v','error','-nostdin','-y','-ss',str(start),'-i',str(path)]
            if not has_audio:command+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
            if narration is not None and narration[i]:
                voice, delay, tempo = narration[i]
                command += ['-i', str(voice)]
            command+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-t',str(duration),'-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(target)]
            if grades is not None:
                saturation = {'natural': .90, 'vivid': 1, 'monochrome': .12}[grades[i]]
                command[command.index('-vf') + 1] += f',eq=saturation={saturation}'
            if narration is not None and narration[i]:
                bed = '0:a:0' if has_audio else '1:a:0'
                voice_track = '1:a:0' if has_audio else '2:a:0'
                mix = (f'[{bed}]volume=0.5[bed];[{voice_track}]aformat=channel_layouts=mono,'
                       f'pan=stereo|c0=0.707*c0|c1=0.707*c0,atempo={tempo:.6f},adelay={delay}:all=1[vo];'
                       '[bed][vo]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.85[mix]')
                command[command.index('-map') + 3] = '[mix]'
                command[-1:-1] = ['-filter_complex', mix]
            elif cuts is not None and has_audio:
                command[-1:-1] = ['-af', 'loudnorm=I=-16:TP=-1.5:LRA=11']
            subprocess.run(command,capture_output=True,check=True,timeout=180)
            segments.append(f"file '{target.name}'")
        manifest=work/'concat.txt';manifest.write_text('\n'.join(segments))
        subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','1','-i',str(manifest),'-c','copy','-movflags','+faststart',str(output)],capture_output=True,check=True,timeout=180)
    except subprocess.SubprocessError as exc:raise RuntimeError('视频合成失败，请检查镜头格式与时长。') from exc
    finally:shutil.rmtree(work)


@bp.post('/assemble')
def assemble():
    data=request.json or {};ids=data.get('job_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=60 or len(set(ids))!=len(ids):raise ValueError('请选择 1–60 个不同镜头，按剪辑顺序提交。')
    selected=[get(i) for i in ids]
    ratio=selected[0]['ratio']
    if any(j.get('ratio')!=ratio for j in selected):raise ValueError('请选择相同画幅的镜头。')
    paths=[download_job(j) for j in selected]
    job_id=str(uuid.uuid4());output=ROOT/(job_id+'.mp4')
    assemble_paths(paths,output,ratio)
    job={'id':job_id,'status':'completed','kind':'assembly','shot_id':'成片','ratio':ratio,'job_ids':ids,'archived':True,'created_at':time.time(),'config':{'provider':'ffmpeg','model':'FFmpeg H.264'},'duration_seconds':sum(j['duration_seconds'] for j in selected)}
    put(job,insert=True)
    return jsonify(public(job))


def register(app,reader):
    global workspace_reader
    workspace_reader=reader
    app.register_blueprint(bp)
