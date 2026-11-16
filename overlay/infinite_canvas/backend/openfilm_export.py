"""Create and render an editable OpenFilm project from accepted novel-film takes."""
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import local_story_gateway

TOOLS = Path.home() / '.local' / 'share' / 'morpho' / 'tools'
OPENFILM = Path(os.environ.get('MORPHO_OPENFILM_BIN', TOOLS / 'openfilm' / 'node_modules' / '.bin' / 'openfilm'))
NODE_DIR = Path(os.environ.get('MORPHO_OPENFILM_NODE_DIR', TOOLS / 'node-22.18' / 'bin'))
MOTIFS = {'arch', 'orbit', 'fault', 'horizon'}
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
SVG_PATH = re.compile(r'^[MLQCZmlqcz0-9,. +\-]+$')


def check_ready():
    if not OPENFILM.is_file() or not (NODE_DIR / 'node').is_file():
        raise ValueError('本机尚未安装 OpenFilm 与配套 Node，请先完成本地剪辑环境安装。')
    if not local_story_gateway.available():
        raise ValueError('代码画面需要本机 GPT-6 Luna 文本接口，请先配置本地密钥。')
    result = subprocess.run([str(NODE_DIR / 'node'), '--version'], capture_output=True, text=True, timeout=5, check=True)
    version = tuple(int(part) for part in result.stdout.strip().lstrip('v').split('.')[:3])
    if version < (22, 18, 0):
        raise ValueError('OpenFilm 需要 Node 22.18 或更新版本。')
    env = dict(os.environ, PATH=str(NODE_DIR) + os.pathsep + os.environ.get('PATH', ''))
    release = subprocess.run([str(OPENFILM), '--version'], env=env,
                             capture_output=True, text=True, timeout=10, check=True)
    if release.stdout.strip() != '0.3.2':
        raise ValueError('OpenFilm 版本与当前工程格式不符，请安装 0.3.2。')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('OpenFilm 成片需要本机 FFmpeg 和 FFprobe。')
    browsers = list((Path.home() / '.cache' / 'ms-playwright').glob(
        'chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell'))
    if not browsers:
        raise ValueError('本机尚未下载 OpenFilm 浏览器，请先运行 openfilm open。')
    libraries = subprocess.run(['ldd', str(browsers[-1])], capture_output=True, text=True, timeout=10, check=True)
    if 'not found' in libraries.stdout:
        raise ValueError('OpenFilm 浏览器缺少系统运行库，请先补齐后再制作。')
    if not (Path('/usr/share/fonts/opentype/noto') / 'NotoSansCJK-Regular.ttc').is_file():
        raise ValueError('本机缺少 Noto Sans CJK 字体，无法制作中文字卡。')


def validate_plan(raw):
    if not isinstance(raw, dict) or raw.get('motif') not in MOTIFS or raw.get('position') not in {'left', 'right'}:
        raise ValueError('代码画面的图形方案无效。')
    path = raw.get('svg_path')
    if (not isinstance(path, str) or not 12 <= len(path) <= 500
            or not path.lstrip().startswith(('M', 'm')) or not SVG_PATH.fullmatch(path)
            or not re.search(r'[LQClqc]', path)
            or any(abs(float(value)) > 180 for value in re.findall(r'[-+]?\d+(?:\.\d+)?', path))):
        raise ValueError('代码画面的矢量图形无效。')
    palette = raw.get('palette')
    if not isinstance(palette, dict) or set(palette) != {'background', 'foreground', 'accent'} or any(
            not isinstance(value, str) or not HEX.fullmatch(value) for value in palette.values()):
        raise ValueError('代码画面的配色方案无效。')
    def luminance(value):
        rgb = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in rgb]
        return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(palette['background']), luminance(palette['foreground'])), reverse=True)
    if (light + .05) / (dark + .05) < 7:
        raise ValueError('代码画面的字色与底色对比不足。')
    return {'motif': raw['motif'], 'position': raw['position'], 'palette': palette, 'svg_path': path}


def generate_plan(package, shots, directory):
    plan_file = directory / 'motion-plan.json'
    if plan_file.is_file():
        return validate_plan(json.loads(plan_file.read_text(encoding='utf-8')))
    bible = package['bible']
    film = package['film']
    prompt = (
        '为中文小说影片末尾两秒设计一个克制而有辨识度的代码生成画面。它必须与已定镜头媒介一致，'
        '不画真人、不用泛滥霓虹、塑料光泽、通用科幻 HUD、粒子雨或装饰性炫技。'
        '仅选择一个与故事世界规则有关系的抽象图形。图形只占少量画面，保留大片负空间。'
        '请根据全片配色提供底色 background、字色 foreground、少量强调色 accent；字色与底色须有很强对比。'
        '只返回 JSON，字段 motif 为 arch/orbit/fault/horizon 之一，position 为 left/right，'
        'palette 为包含 background、foreground、accent 的三个 #RRGGBB 色值。'
        'svg_path 是你亲自设计的一条 SVG 路径代码，只用 M L Q C Z 指令和 -180 到 180 的坐标，'
        '画一个抽象但与故事有关的单线图形。禁止闭合填色、文字、人物脸、随机装饰。\n'
        + json.dumps({'书名': film['source_title'], '造型媒介': bible.get('visual_medium'),
                      '视觉概念': bible.get('concept'), '配色': bible.get('color_script'),
                      '末镜': {key: shots[-1].get(key) for key in ('beat', 'composition', 'lighting', 'grade', 'handoff')}},
                     ensure_ascii=False))
    plan = validate_plan(local_story_gateway.generate_json(prompt, 700))
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    return plan


def graphic_page(title, plan, duration, width, height):
    palette = plan['palette']
    alignment = 'left:8%' if plan['position'] == 'left' else 'right:8%'
    motif_x = '78%' if plan['position'] == 'left' else '22%'
    safe_title = html.escape(title[:32])
    title_size = max(20, min(82 * width / 1280, width * .61 / max(1, len(title[:32]))))
    motion = {
        'arch': 'motif.style.transform=`translateY(${(1-p)*42}px)`;',
        'orbit': 'motif.style.transform=`rotate(${(p-.5)*16}deg)`;',
        'fault': 'motif.style.transform=`translateX(${(1-p)*28}px)`;',
        'horizon': 'motif.style.transform=`scaleX(${.86+p*.14})`;',
    }[plan['motif']]
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width={width}, height={height}">
<style>
@font-face{{font-family:NotoFilm;src:url("assets/NotoSansCJK-Regular.ttc") format("truetype");font-weight:400}}
html,body{{margin:0;width:100%;height:100%;overflow:hidden}}
#card{{position:absolute;inset:0;background:{palette['background']};color:{palette['foreground']};opacity:0}}
#rule{{position:absolute;top:21%;{alignment};width:5.5%;height:3px;background:{palette['accent']}}}
#title{{position:absolute;top:47%;{alignment};font:400 {title_size:.1f}px/1.2 NotoFilm,sans-serif;letter-spacing:.13em;max-width:62%;white-space:normal;word-break:break-all}}
#motif{{position:absolute;top:50%;left:{motif_x};width:17%;overflow:visible;color:{palette['accent']}}}
</style></head><body><div id="card"><div id="rule"></div><div id="title">{safe_title}</div>
<svg id="motif" viewBox="-200 -200 400 400" aria-hidden="true"><path d="{html.escape(plan['svg_path'], quote=True)}" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="square"/></svg></div>
<script>const card=document.getElementById('card');const motif=document.getElementById('motif');
window.film={{duration:{duration:.3f},ready:document.fonts.load('{title_size:.1f}px NotoFilm'),frame(t){{
const p=Math.max(0,Math.min(1,t/{duration:.3f}));
card.style.opacity=String(Math.max(0,Math.min(1,(p-.08)/.28)));
{motion}
}}}};</script></body></html>'''


def shot_page(frame_dir, count, duration, width, height):
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width={width}, height={height}">
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#0d1013}}img{{width:100%;height:100%;object-fit:contain}}</style>
</head><body><img id="frame" src="assets/{frame_dir}/00001.jpg"><script>
const image=document.getElementById('frame');
window.film={{duration:{duration:.3f},async frame(t){{
const index=Math.max(1,Math.min({count},Math.floor(t*24)+1));
const source=`assets/{frame_dir}/${{String(index).padStart(5,'0')}}.jpg`;
if(image.getAttribute('src')!==source)image.src=source;
await image.decode();
}}}};</script></body></html>'''


def make_project(package, shots, choices, source, work, on_progress=None):
    project = work / 'openfilm'
    assets = project / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height', '-of', 'json', str(source)],
                           capture_output=True, text=True, check=True, timeout=30)
    stream = json.loads(probe.stdout)['streams'][0]
    width, height = int(stream['width']), int(stream['height'])
    if width < 320 or height < 320:
        raise ValueError('成片画幅过小，无法制作可编辑工程。')
    cuts = []
    elapsed = 0.0
    for index, ((path, (start, end)), shot) in enumerate(zip(choices, shots, strict=True), 1):
        if on_progress:
            on_progress(f'正在整理第 {index}/{len(shots)} 镜的可编辑画面')
        target = assets / f'source-{index:03d}.mp4'
        if not target.is_file() or target.stat().st_size != path.stat().st_size:
            shutil.copy2(path, target)
        frame_name = f'frames-{index:03d}'
        frames = assets / frame_name
        expected = round((end - start) * 24)
        if expected < 1:
            raise ValueError('镜头剪辑时长不足一帧。')
        if not frames.is_dir() or abs(len(list(frames.glob('*.jpg'))) - expected) > 1:
            temporary_frames = assets / (frame_name + '.part')
            if temporary_frames.exists():
                shutil.rmtree(temporary_frames)
            temporary_frames.mkdir()
            try:
                subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(path),
                                '-ss', f'{start:.3f}', '-t', f'{end-start:.3f}',
                                '-vf', f'fps=24,scale={width}:{height}:force_original_aspect_ratio=decrease',
                                '-q:v', '2', str(temporary_frames / '%05d.jpg')],
                               capture_output=True, check=True, timeout=300)
                count = len(list(temporary_frames.glob('*.jpg')))
                if abs(count - expected) > 1:
                    raise ValueError(f'第 {index} 镜的画面帧数与剪辑时长不一致。')
                if frames.exists():
                    shutil.rmtree(frames)
                temporary_frames.rename(frames)
            finally:
                if temporary_frames.exists():
                    shutil.rmtree(temporary_frames)
        count = len(list(frames.glob('*.jpg')))
        (project / f'shot-{index:03d}.html').write_text(
            shot_page(frame_name, count, end - start, width, height), encoding='utf-8')
        cuts.append({'shot_id': shot['id'], 'file': target.name, 'frames': frame_name,
                     'page': f'shot-{index:03d}.html', 'source_in': start,
                     'source_out': end, 'at': round(elapsed, 3), 'grade': shot.get('grade', 'natural')})
        elapsed += end - start
    if elapsed < 2:
        raise ValueError('代码画面的成片时长不足。')
    audio = assets / 'mix.m4a'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-map', '0:a:0',
                    '-vn', '-c:a', 'copy', str(audio)], check=True, capture_output=True, timeout=180)
    fonts = list(Path('/usr/share/fonts/opentype/noto').glob('NotoSansCJK-Regular.ttc'))
    if len(fonts) != 1:
        raise ValueError('本地缺少 Noto Sans CJK 字体，无法制作中文字卡。')
    font_target = assets / fonts[0].name
    if not font_target.is_file():
        shutil.copy2(fonts[0], font_target)
    plan = generate_plan(package, shots, project)
    card_duration = min(2.3, cuts[-1]['source_out'] - cuts[-1]['source_in'])
    card_start = elapsed - card_duration
    (project / 'graphic.html').write_text(graphic_page(package['film']['source_title'], plan, card_duration, width, height), encoding='utf-8')
    size = f'left:0px;top:0px;width:{width}px;height:{height}px'
    clips = []
    for cut in cuts:
        saturation = {'natural': .9, 'vivid': 1, 'monochrome': .12}[cut['grade']]
        clips.append(f'<iframe id="{html.escape(cut["shot_id"], quote=True)}" '
                     f'src="{cut["page"]}#t=0,{cut["source_out"]-cut["source_in"]:.3f}" '
                     f'at="{cut["at"]:.3f}" style="{size};filter:saturate({saturation})"></iframe>')
    html_text = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                 f'<meta name="viewport" content="width={width}, height={height}"></head><body>'
                 f'<section><iframe src="graphic.html#t=0,{card_duration:.3f}" at="{card_start:.3f}" '
                 f'style="{size}"></iframe></section>'
                 '<section>' + '\n'.join(clips) + '</section>'
                 '<section><audio src="assets/mix.m4a" volume="1"></audio></section></body></html>')
    (project / 'film.html').write_text(html_text, encoding='utf-8')
    (project / 'edit.json').write_text(json.dumps({'source_title': package['film']['source_title'],
        'visual_medium': package['bible'].get('visual_medium'), 'color_script': package['bible'].get('color_script'),
        'shots': cuts}, ensure_ascii=False, indent=2), encoding='utf-8')
    return project, elapsed


def render(project, output, expected_duration, on_progress=None):
    check_ready()
    env = dict(os.environ, PATH=str(NODE_DIR) + os.pathsep + os.environ.get('PATH', ''))
    command = [str(OPENFILM)]
    if on_progress:
        on_progress('正在检查可编辑画面的逐帧一致性')
    for name, args, limit in (
            ('look', ['look'], 180),
            ('render', ['render', '--out', str(output), '--fps', '24', '--workers', '3'], 3600)):
        if name == 'render' and on_progress:
            on_progress('正在导出最终画面与声音')
        try:
            result = subprocess.run(command + args, cwd=project, env=env,
                                    capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f'OpenFilm {name} 超时，剪辑工程已保留。') from exc
        if result.returncode:
            log = project / '.film' / 'last-error.log'
            log.parent.mkdir(exist_ok=True)
            log.write_text((result.stdout + '\n' + result.stderr)[-12000:], encoding='utf-8')
            raise ValueError(f'OpenFilm {name} 未通过，剪辑工程和诊断记录已保留。')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(output)],
                            check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    kinds = {stream.get('codec_type') for stream in data.get('streams', [])}
    duration = float(data.get('format', {}).get('duration', 0))
    if not {'video', 'audio'} <= kinds or abs(duration - expected_duration) > .12:
        raise ValueError('OpenFilm 导出的画面或声音不完整，工程文件已保留。')
    return output
