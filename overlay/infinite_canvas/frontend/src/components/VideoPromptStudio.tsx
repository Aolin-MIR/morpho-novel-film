import {Locale,resolveInitialLocale} from '../studioLocale';
import {entryText,entryMessage} from '../entryLocale';
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API_BASE } from '../apiBase';
import {systemMessage} from '../utils/systemMessage';
import ModelKeyControl from './ModelKeyControl';
import VideoWorkflow from './VideoWorkflow';
import NovelFilmRender from './NovelFilmRender';
import './VideoPromptStudio.css';

export const defaultVideoOptions = { adapter:'wan', style:'cinema', custom_style:'', aspect_ratio:'16:9', mode:'image', duration:6, model:'qwen3.8-flash', scope:'all', scene_ids:[] as string[] };
export type VideoOptions = typeof defaultVideoOptions;
const entries = (v:any):any[] => Array.isArray(v) ? v : Object.values(v || {});
const errorText = (e:any) => systemMessage(e.response?.data?.error || e.message || e);

export function VideoOptionsForm({value,onChange,scenes=[],disabled=false,locale=resolveInitialLocale(),novel=false}:{value:VideoOptions;onChange:(v:VideoOptions)=>void;scenes?:any[];disabled?:boolean;locale?:Locale;novel?:boolean}) {
  const t=(text:string)=>entryText(locale,text);
  const [catalog,setCatalog]=useState<any>(null);
  const [error,setError]=useState('');
  useEffect(()=>{const controller=new AbortController();axios.get(`${API_BASE}/video-prompts`,{signal:controller.signal}).then(r=>setCatalog(r.data)).catch(e=>{if(!controller.signal.aborted)setError(entryMessage(locale,errorText(e)));});return()=>controller.abort();},[]);
  const patch=(v:Partial<VideoOptions>)=>onChange({...value,...v});
  return <fieldset className="video-options" disabled={disabled}>
    <legend>{t("视频制作方案")}</legend>
    {error&&<p role="alert">{error}</p>}
    <div className="video-options__grid">
      {!novel && <label>{t("提示词体系")}<select value={value.adapter} onChange={e=>patch({adapter:e.target.value})}>{catalog?.adapters.map((a:any)=><option key={a.id} value={a.id}>{t(adapterLabels[a.id])}</option>)}</select></label>}
      <label>{t("视觉风格")}<select value={value.style} onChange={e=>patch({style:e.target.value})}>{catalog?.styles.map((s:any)=><option key={s.id} value={s.id}>{t(s.label)}</option>)}</select></label>
      <label>{t("画幅")}<select value={value.aspect_ratio} onChange={e=>patch({aspect_ratio:e.target.value})}><option value="16:9">{t("16:9 横屏电影")}</option><option value="9:16">{t("9:16 竖屏短剧")}</option></select></label>
      {!novel && <label>{t("制作方式")}<select value={value.mode} onChange={e=>patch({mode:e.target.value})}><option value="image">{t("参考首帧 → 图生视频（推荐）")}</option><option value="text">{t("完整画面 → 文生视频")}</option></select></label>}
      {!novel && <label>{t("每镜目标时长")}<select value={value.duration} onChange={e=>patch({duration:Number(e.target.value)})}>{[4,6,8].map(n=><option key={n} value={n}>{n} {t("秒")}</option>)}</select></label>}
      <label>{t("提示词编写方式")}<select value={value.model} onChange={e=>patch({model:e.target.value})}><option value="qwen3.8-flash">{t("快速编写")}</option><option value="qwen3.8-max-0902">{t("深入编写")}</option></select></label>
    </div>
    <p>{t(catalog?.styles.find((s:any)=>s.id===value.style)?.guide||'')} {t(catalog?.adapters.find((a:any)=>a.id===value.adapter)?.guide||'')}</p>
    <label>{t("自定义风格与制作要求")}<textarea value={value.custom_style} maxLength={3000} onChange={e=>patch({custom_style:e.target.value})} placeholder={t("例如：雨夜渡口，青灰色调、钨丝灯暖光；克制表演；避免过度手持。可覆盖预设细节。")} /></label>
    {!!scenes.length&&<><label>{t("生成范围")}<select value={value.scope} onChange={e=>patch({scope:e.target.value})}><option value="all">{t("全部场景")}</option><option value="selected">{t("选择场景")}</option></select></label>{value.scope==='selected'&&<div className="video-scene-picks">{scenes.map(s=><label key={s.id}><input type="checkbox" checked={value.scene_ids.includes(s.id)} onChange={e=>patch({scene_ids:e.target.checked?[...value.scene_ids,s.id]:value.scene_ids.filter(id=>id!==s.id)})}/>{s.title||s.name||s.id}</label>)}</div>}</>}
  </fieldset>;
}

function download(value:string,name:string,mime:string) {const url=URL.createObjectURL(new Blob([value],{type:mime}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
const adapterLabels:Record<string,string>={minimax:'MiniMax H3',wan:'时间轴分镜',seedance:'分镜叙事',kling:'动作镜头',veo:'构图与声画',runway:'首帧运动'};
const sourceTitles:Record<string,string>={
  'https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-prompt-guide':'时间轴提示词指南',
  'https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide':'首帧运动提示词指南',
  'https://deepmind.google/models/veo/prompt-guide/':'声画描述提示词指南',
  'https://arxiv.org/abs/2604.09195':'镜头控制研究',
  'https://arxiv.org/abs/2605.23508':'视频运动研究',
};
const sourceTitle=(source:any)=>sourceTitles[source.url]||'查看文献';
const trailerBeatLabels: Record<string, string> = {
  hook: '开场钩子', world: '世界揭示', pressure: '危机升级', breath: '停顿', cliffhanger: '最后的悬念',
};
function markdown(p:any) {
  const narration = p.film?.narration;
  const musicCredits = p.film?.music_credits || [];
  return ['# 视频制作包', `画幅 ${p.options.aspect_ratio}`, '## 视觉设定', p.bible.concept,
    p.bible.visual_medium, p.bible.color_script,
    p.bible.visual_style, p.bible.continuity_rules,
    ...p.bible.assets.flatMap((a:any) => [`### ${a.name}`, a.design_note, a.signature, a.material_logic, a.image_prompt]),
    ...p.scenes.flatMap((s:any) => [`## ${s.title}`, s.coverage_note,
      ...(s.sound_effects || []).map((cue:any) => `- 音效 ${cue.source} · ${cue.shot_id} · ${cue.offset} 秒入点 · ${cue.duration} 秒 · ${cue.space} / ${cue.distance} · 声像 ${cue.pan}`),
      ...s.shots.flatMap((shot:any) => [
      `### ${shot.id} · ${shot.duration_seconds} 秒`, shot.beat, shot.source_excerpt,
      shot.composition, shot.lighting, shot.grade_reason, shot.color_design, shot.screen_action, shot.handoff,
      ...(shot.spectacle ? ['视觉奇观', shot.spectacle.setup, shot.spectacle.reveal, shot.spectacle.payoff, shot.spectacle.protected] : []),
      `素材 ${shot.reference_ids.join('、')}`,
      shot.image_prompt, shot.video_prompt, shot.last_frame, shot.dialogue, shot.sound,
      `${shot.continuity} ${shot.transition}`, shot.quality_check,
    ])]),
    ...(narration ? ['## 旁白台本', narration.voice, ...narration.cues.map((cue:any) =>
      `- ${cue.timeline_start.toFixed(1)} 秒 · ${cue.text}（${cue.delivery}，${cue.sound_bridge}）`)] : []),
    ...(musicCredits.length ? ['## 音乐署名', ...musicCredits.map((track:any) =>
      `- [${track.title}](${track.source}) · ${track.artist} · [${track.license}](${track.license_url}) · 剪辑与混音`)] : []),
    ...(p.sources.length ? ['## 方法来源', ...p.sources.map((s:any) => `- [${sourceTitle(s)}](${s.url})`)] : []),
  ].filter(Boolean).join('\n\n');
}

export default function VideoPromptStudio({workspace,onClose,onUpdated}:{workspace:any;onClose:()=>void;onUpdated:(payload:any)=>void}) {
  const [settings,setSettings]=useState<VideoOptions>(workspace.workspace_meta?.video_package?.film ? {...defaultVideoOptions, aspect_ratio: workspace.workspace_meta.video_package.options.aspect_ratio} : workspace.workspace_meta?.video_package?.options || defaultVideoOptions);
  const [data,setData]=useState<any>(null);
  const [filmKind, setFilmKind] = useState<'script' | 'trailer' | 'scene' | 'feature'>(workspace.workspace_meta?.video_package?.film?.kind || 'script');
  const [novelText, setNovelText] = useState('');
  const [novelFile, setNovelFile] = useState<File | null>(null);
  const [error,setError]=useState('');
  const [runId,setRunId]=useState('');
  const [submitting,setSubmitting]=useState(false);
  const [progress,setProgress]=useState('');
  const [started,setStarted]=useState(0);
  const [seconds,setSeconds]=useState(0);
  const [changes,setChanges]=useState<Record<string,Record<string,string>>>({});
  const [notice,setNotice]=useState('');
  const load=()=>axios.get(`${API_BASE}/video-prompts`).then(r=>{setData(r.data);if(r.data.active_run){setRunId(r.data.active_run.id);setStarted(Date.now());}});
  useEffect(()=>{load().catch(e=>setError(errorText(e)));},[]);
  useEffect(()=>{if(!started)return;const t=setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>clearInterval(t);},[started]);
  useEffect(()=>{
    if(!runId)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try {const {data:r}=await axios.get(`${API_BASE}/api/generation-runs/${runId}`,{signal:controller.signal});
      setProgress(systemMessage([r.latest_event?.title,r.latest_event?.detail].filter(Boolean).join(' · ')));
      if(r.status==='completed'){setRunId('');setStarted(0);setChanges({});onUpdated(r.result_payload.structured_story_payload);await load();return;}
      if(r.status==='failed'){setRunId('');setStarted(0);setError(systemMessage(r.error_text||'制作包生成失败，请重试。'));return;}
    }catch(e){if(controller.signal.aborted)return;setError(errorText(e));}
    if(!controller.signal.aborted)timer=setTimeout(poll,3000);};poll();
    return()=>{controller.abort();clearTimeout(timer);};
  // The run owns its source workspace until completion; only restart polling for a new run.
  },[runId]);
  const generate = async () => {
    setError('');
    setSubmitting(true);
    try {
      let payload: FormData | { video_options: VideoOptions } = { video_options: settings };
      if (filmKind !== 'script') {
        const form = new FormData();
        form.set('film_kind', filmKind);
        form.set('video_options', JSON.stringify({ ...settings, adapter: 'minimax', mode: 'references' }));
        if (novelFile) form.set('file', novelFile);
        else form.set('source', novelText);
        payload = form;
      }
      const { data: result } = await axios.post(`${API_BASE}/video-prompts/generate`, payload);
      if (!result.run_id) throw new Error('无法启动分镜，请重试。');
      setRunId(result.run_id);
      setStarted(Date.now());
      setSeconds(0);
      setProgress('正在准备分镜');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  };
  const packageData=data?.package;
  const ready=packageData?.status==='completed';
  const update=(id:string,key:string,value:string)=>setChanges(c=>({...c,[id]:{...c[id],[key]:value}}));
  const effective=ready?{...packageData,bible:{...packageData.bible,assets:packageData.bible.assets.map((a:any)=>({...a,...changes[a.id]}))},scenes:packageData.scenes.map((s:any)=>({...s,shots:s.shots.map((shot:any)=>({...shot,...changes[shot.id]}))}))}:null;
  const save=async()=>{setError('');try{const r=await axios.patch(`${API_BASE}/video-prompts`,{created_at:packageData.created_at,edited_at:packageData.edited_at,changes});setData(r.data);setChanges({});onUpdated({...workspace,workspace_meta:{...workspace.workspace_meta,video_package:r.data.package}});setNotice('修改已保存。');}catch(e){setError(errorText(e));}};
  const copy=async(value:string)=>{try{await navigator.clipboard.writeText(value);setNotice('提示词已复制。');}catch{setError('浏览器不允许复制，请选择文本手动复制。');}};
  return <div className="video-studio-backdrop"><section className="video-studio" role="dialog" aria-modal="true" aria-label="视频制作台">
    <header><div><h2>视频制作台</h2></div><button onClick={onClose}>关闭</button></header>
    <div className="video-studio__body"><aside>
      <label>要制作什么
        <select value={filmKind} disabled={Boolean(runId) || submitting} onChange={event => {
          const kind = event.target.value as typeof filmKind;
          setFilmKind(kind);
          if (kind !== 'script') setSettings(current => ({ ...current,
            style: current.style === 'cinema' ? 'printmaking' : current.style,
            aspect_ratio: kind === 'feature' ? '16:9' : '9:16' }));
        }}>
          <option value="script">已有剧本分镜</option>
          <option value="trailer">全书预告片（45–60 秒）</option>
          <option value="scene">单场剧情短片</option>
          <option value="feature">全书改编长片（10 分钟以上）</option>
        </select>
      </label>
      {filmKind !== 'script' && <>
        <label>上传小说<input type="file" accept=".txt,.md,.markdown,.epub"
          disabled={Boolean(runId) || submitting} onChange={event => setNovelFile(event.target.files?.[0] || null)} /></label>
        {!novelFile && <label>或粘贴正文<textarea value={novelText} maxLength={480000} rows={6}
          disabled={Boolean(runId) || submitting} onChange={event => setNovelText(event.target.value)} /></label>}
      </>}
      <VideoOptionsForm novel={filmKind !== 'script'} value={settings} onChange={setSettings} scenes={filmKind === 'script' ? entries(workspace.scenes) : []} disabled={!!runId||submitting}/><ModelKeyControl/>
      <button className="video-primary" onClick={generate} disabled={!!runId || submitting || !data || (filmKind !== 'script' && !novelFile && !novelText.trim())}>{runId ? '正在生成分镜…' : '生成分镜'}</button>
      {(runId||submitting)&&<div role="status"><strong>{progress}</strong><p>已等待 {seconds} 秒。关闭后可回来继续查看。</p></div>}
      {error&&<p className="video-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      <details><summary>制作步骤与方法来源</summary><ol>{data?.steps.map((s:string)=><li key={s}>{s}</li>)}</ol>{data?.sources.map((s:any)=><p key={s.url}><a href={s.url} target="_blank" rel="noreferrer">{sourceTitle(s)} ↗</a></p>)}</details>
    </aside><main>
      {packageData?.status==='failed'&&<p className="video-error">剧本已生成，但视频制作包失败：{systemMessage(packageData.error)}。可在此单独重试。</p>}
      {data?.stale&&<p className="video-error">剧本或结构已变化。下方是旧版制作包，请重新生成后再用于视频制作。</p>}
      {!effective?.film && <NovelFilmRender blocked />}
      {!ready&&<div className="video-empty"><h3>把故事变成可以拍摄的镜头</h3><p>选择风格与画幅，生成完整的分镜制作包。已有剧本可直接使用，不必重写。</p><ol>{data?.steps.map((s:string)=><li key={s}>{s}</li>)}</ol></div>}
      {ready&&<><div className="video-actions"><strong>{effective.scenes.length} 场 · {effective.scenes.reduce((n:number,s:any)=>n+s.shots.length,0)} 镜</strong><button onClick={save} disabled={data.stale||!Object.keys(changes).length}>保存提示词修改</button><button onClick={()=>download(JSON.stringify(effective,null,2),'morpho-video-package.json','application/json')}>导出 JSON</button><button onClick={()=>download(markdown(effective),'morpho-video-package.md','text/markdown')}>导出制作手册</button></div>
      {effective.film ? <>
        <article><h3>{effective.film.source_title}</h3>
          {effective.bible.concept && <p>{effective.bible.concept}</p>}
          <p>{effective.film.hook}</p><p>{effective.film.ending}</p>
          {effective.film.audience_question && <p>{effective.film.audience_question}</p>}
          {effective.film.narration && <details>
            <summary>画外音 · 旁白台本</summary>
            <p>{effective.film.narration.voice}</p>
            <ol>{effective.film.narration.cues.map((cue: any) => <li key={cue.shot_id}>
              <label>{cue.timeline_start.toFixed(1)} 秒<textarea value={changes[cue.shot_id]?.narration_text ?? cue.text}
                maxLength={160} onChange={event => update(cue.shot_id, 'narration_text', event.target.value)} /></label>
              <p>{cue.delivery} · {cue.sound_bridge}</p>
            </li>)}</ol>
          </details>}
          {!!effective.film.music_credits?.length && <details>
            <summary>配乐与署名</summary>
            <ul>{effective.film.music_credits.map((track: any) => <li key={track.source}>
              <a href={track.source} target="_blank" rel="noreferrer">{track.title}</a> · {track.artist} · {track.license}
            </li>)}</ul>
          </details>}
          {effective.film.passage && <details><summary>选中的小说情节</summary><p>{effective.film.passage.reason}</p>
            <blockquote>{effective.film.passage.excerpt}</blockquote>
          </details>}
          {effective.film.trailer_moments && <details><summary>预告片选用的原著情节</summary>
            {Object.entries<{chapter: string; action: string; excerpt: string}>(effective.film.trailer_moments).map(([id, moment]) =>
              <article key={id}><h4>{moment.chapter}</h4><p>{moment.action}</p><blockquote>{moment.excerpt}</blockquote></article>)}
          </details>}
        </article>
        <NovelFilmRender key={packageData.created_at} createdAt={packageData.created_at} editedAt={packageData.edited_at}
          shotCount={effective.scenes.reduce((sum:number, scene:any) => sum + scene.shots.length, 0)} blocked={Boolean(data.stale) || Object.keys(changes).length > 0} />
      </> : <VideoWorkflow key={packageData.created_at} packageData={effective} blocked={Boolean(data.stale)||Object.keys(changes).length>0}/>}
      <article><h3>全片视觉设定</h3><p>{effective.bible.visual_style}</p>
        {effective.bible.visual_medium && <p>{effective.bible.visual_medium}</p>}
        {effective.bible.color_script && <p>{effective.bible.color_script}</p>}<p>{effective.bible.continuity_rules}</p></article>
      <details><summary>人物、场景与道具参考素材 · {effective.bible.assets.length} 项</summary>{effective.bible.assets.map((a:any)=><article key={a.id}><h3>{a.name}</h3><p>{a.design_note}</p>{a.signature && <p>{a.signature}</p>}{a.material_logic && <p>{a.material_logic}</p>}<label>参考图提示词<textarea value={a.image_prompt} onChange={e=>update(a.id,'image_prompt',e.target.value)}/></label><button onClick={()=>copy(a.image_prompt)}>复制参考图提示词</button></article>)}</details>
      {effective.scenes.map((scene:any) => <section key={scene.scene_id}>
        <h3 className="video-scene-title">{scene.title}</h3><p>{scene.coverage_note}</p>
        {!!scene.sound_effects?.length && <details><summary>空间音效</summary>
          <ul>{scene.sound_effects.map((cue:any) => <li key={`${cue.shot_id}-${cue.offset}`}>
            {cue.source} · {cue.pan < -.15 ? '左侧' : cue.pan > .15 ? '右侧' : '居中'} ·
            {cue.distance === 'far' ? '远处' : cue.distance === 'near' ? '近处' : '中距离'} ·
            {cue.offset < 0 ? `画面前 ${-cue.offset} 秒进入` : `切入后 ${cue.offset} 秒进入`}
          </li>)}</ul>
        </details>}
        {scene.shots.map((shot:any, index:number) => <article key={shot.id} className="video-shot">
          <div className="video-actions"><h3>镜头 {index + 1} · {shot.clip_seconds ?? shot.duration_seconds} 秒
            {shot.trailer_beat ? ` · ${trailerBeatLabels[shot.trailer_beat]}` : ''}</h3>
            <button onClick={() => copy(shot.video_prompt)} disabled={Boolean(changes[shot.id]?.screen_action)}>复制视频提示词</button>
          </div>
          <p><strong>{shot.beat}</strong></p><blockquote>{shot.source_excerpt}</blockquote>
          {shot.screen_action && <details>
            <summary>拍法、构图与声音</summary>
            <label>画面实际呈现的动作<textarea value={shot.screen_action} maxLength={1500}
              disabled={Boolean(changes[shot.id]?.video_prompt)} onChange={event => update(shot.id, 'screen_action', event.target.value)} /></label>
            <label>剪辑与声音如何承接<textarea value={shot.handoff} maxLength={1500}
              onChange={event => update(shot.id, 'handoff', event.target.value)} /></label>
            <p>{shot.composition}</p><p>{shot.lighting}</p><p>{shot.grade_reason}</p>
            {shot.color_design && <p>{shot.color_design}</p>}
            {shot.spectacle && <><h4>视觉奇观</h4><p>{shot.spectacle.setup}</p><p>{shot.spectacle.reveal}</p>
              <p>{shot.spectacle.payoff}</p><p>保持稳定：{shot.spectacle.protected}</p></>}
          </details>}
          <label>首帧画面提示词<textarea value={shot.image_prompt} onChange={event => update(shot.id, 'image_prompt', event.target.value)} /></label>
          <label>视频生成提示词<textarea value={shot.video_prompt} disabled={Boolean(changes[shot.id]?.screen_action)}
            onChange={event => update(shot.id, 'video_prompt', event.target.value)} /></label>
          <details><summary>末帧、对白和切点</summary><p>{shot.last_frame}</p><p>{shot.dialogue || '无对白'}</p>
            <p>{shot.sound}</p><p>{shot.continuity} {shot.transition}</p><p>{shot.quality_check}</p>
          </details>
        </article>)}
      </section>)}
      </>}
    </main></div>
  </section></div>;
}
