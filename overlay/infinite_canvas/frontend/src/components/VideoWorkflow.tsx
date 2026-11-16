import React, {useEffect,useRef,useState} from 'react';
import axios from 'axios';
import {API_BASE} from '../apiBase';
import {systemMessage} from '../utils/systemMessage';

const message=(e:any)=>systemMessage(e.response?.data?.error||e.message||e);
const labels:Record<string,string>={submitting:'正在提交',submission_unknown:'提交结果待核对',queued:'排队中',running:'生成中',completed:'已完成',failed:'失败',canceled:'已取消',expired:'已过期'};
const base=`${API_BASE}/video-workflow`;
export default function VideoWorkflow({packageData,blocked}:{packageData:any;blocked:boolean}) {
  const [catalog,setCatalog]=useState<any>(null);
  const [config,setConfig]=useState(() => ({provider: packageData.options.adapter === 'minimax' ? 'minimax' : 'wan', model: packageData.options.adapter === 'minimax' ? 'MiniMax-H3' : 'wan3.0-video', workspace_id:'',region:'cn-beijing'}));
  const [keys,setKeys]=useState<Record<string,string>>({});
  const [jobs,setJobs]=useState<any[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [frames,setFrames]=useState<Record<string,string>>({});
  const [error,setError]=useState('');
  const [status,setStatus]=useState('');
  const [busy,setBusy]=useState(false);
  const [editOrder,setEditOrder]=useState<string[]>([]);
  const [preview,setPreview]=useState('');
  const [recoverIds,setRecoverIds]=useState<Record<string,string>>({});
  const busyRef=useRef(false);
  const shots=packageData.scenes.flatMap((s:any)=>s.shots);
  const key=keys[config.provider]||'';
  const load=async()=>{const r=await axios.get(`${base}/jobs`);setCatalog(r.data);setJobs(r.data.jobs.filter((job:any) => job.kind !== 'film'));};
  useEffect(()=>{load().catch(e=>setError(message(e)));},[]);
  const refresh=async()=>{
    if(busyRef.current)return;
    busyRef.current=true;
    try{const running=jobs.filter(j=>['queued','running'].includes(j.status)&&j.config.provider===config.provider);
      for(const job of running){await axios.post(`${base}/jobs/${job.id}/poll`,{},{headers:{'X-Video-Api-Key':key},timeout:75000});}
      await load();
    }catch(e){setError(message(e));}finally{busyRef.current=false;}
  };
  useEffect(()=>{if(!key||!jobs.some(j=>['queued','running'].includes(j.status)&&j.config.provider===config.provider))return;const timer=setTimeout(refresh,15000);return()=>clearTimeout(timer);},[key,jobs,config.provider]);
  const upload=async(id:string,file?:File)=>{if(!file)return;setError('');if(file.size>5000000){setError('首帧图片需小于 5 MB。');return;}const reader=new FileReader();reader.onload=()=>setFrames(f=>({...f,[id]:String(reader.result)}));reader.onerror=()=>setError('图片读取失败。');reader.readAsDataURL(file);};
  const submit=async()=>{
    setError('');setBusy(true);
    try{
      if(!key.trim())throw new Error('请填写所选视频服务的 API Key。');
      if(!selected.length)throw new Error('请勾选要生成的镜头。');
      if(packageData.options.mode==='image'&&selected.some(id=>!frames[id]))throw new Error('图生视频需要为每个选中镜头上传首帧。');
      for(let i=0;i<selected.length;i++){
        setStatus(`正在提交 ${i+1}/${selected.length} 镜。服务商可能按生成量计费。`);
        const {data}=await axios.post(`${base}/jobs`,{request_id:crypto.randomUUID(),shot_id:selected[i],config,first_frame:frames[selected[i]],package_created_at:packageData.created_at,package_edited_at:packageData.edited_at},{headers:{'X-Video-Api-Key':key},timeout:90000});
        await load();
        if(data.status==='submission_unknown')throw new Error(data.error+' 已停止后续镜头提交。');
      }
      setSelected([]);setStatus('已提交。页面每 15 秒查询一次；刷新后可重新填密钥继续查询。');
    }catch(e){setError(message(e));await load().catch(()=>{});}finally{setBusy(false);}
  };
  const recover=async(job:any)=>{setBusy(true);setError('');try{await axios.post(`${base}/jobs/${job.id}/recover`,{task_id:recoverIds[job.id]},{headers:{'X-Video-Api-Key':keys[job.config.provider]||''},timeout:75000});await load();}catch(e){setError(message(e));}finally{setBusy(false);}};
  const archive=async(job:any)=>{setBusy(true);setError('');try{setStatus('正在将视频保存到项目…');await axios.post(`${base}/jobs/${job.id}/archive`,{},{timeout:180000});await load();setPreview(job.id);setStatus('视频已保存，可在站内播放和下载。');}catch(e){setError(message(e));}finally{setBusy(false);}};
  const assemble=async()=>{setBusy(true);setError('');setStatus('FFmpeg 正在按所选顺序统一画幅、音轨并合成成片…');try{const {data}=await axios.post(`${base}/assemble`,{job_ids:editOrder},{timeout:600000});await load();setPreview(data.id);setStatus('成片已保存。');}catch(e){setError(message(e));}finally{setBusy(false);}};
  const move=(i:number,delta:number)=>setEditOrder(old=>{const next=[...old];[next[i],next[i+delta]]=[next[i+delta],next[i]];return next;});
  const inFlight=(id:string)=>jobs.some(j=>j.shot_id===id&&['submitting','submission_unknown','queued','running'].includes(j.status));
  return <section className="video-execution"><h3>站内视频生成与成片</h3><p>视频服务密钥独立配置，默认留空，仅保留在此面板内存中。直接连接所选服务，不自动切换。此功能已按接口契约验证，尚未进行真实视频生成验收。</p>
    <div className="video-options__grid"><label>视频服务<select value={config.provider} disabled={busy} onChange={e=>{const provider=e.target.value;setConfig(c=>({...c,provider,model:catalog.providers.find((p:any)=>p.id===provider).models[0]}));setError('');}}>{catalog?.providers.map((p:any)=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
      <label>视频模型<select value={config.model} disabled={busy} onChange={e=>setConfig(c=>({...c,model:e.target.value}))}>{catalog?.providers.find((p:any)=>p.id===config.provider)?.models.map((m:string)=><option key={m} value={m}>{m}</option>)}</select></label>
      <label>{config.provider==='runway'?'Runway API Key':'视频 API Key'}<input type="password" autoComplete="off" value={key} placeholder="默认空，请填写自己的密钥" onChange={e=>setKeys(k=>({...k,[config.provider]:e.target.value}))}/></label>
      {config.provider==='wan'&&<><label>万相 Workspace ID<input value={config.workspace_id} onChange={e=>setConfig(c=>({...c,workspace_id:e.target.value}))} placeholder="来自百炼工作空间"/></label><label>万相地域<select value={config.region} onChange={e=>setConfig(c=>({...c,region:e.target.value}))}>{catalog?.regions.map((r:string)=><option key={r} value={r}>{r}</option>)}</select></label></>}
    </div>
    <p>{config.provider==='runway'?'请填写所选服务平台的密钥。':''} <a href={catalog?.providers.find((p:any)=>p.id===config.provider)?.doc} target="_blank" rel="noreferrer">服务商 API 文档 ↗</a> <button onClick={()=>setKeys({})}>清除视频密钥</button></p>
    {blocked&&<p className="video-error">请先保存提示词修改；剧本变化后请重新生成制作包。</p>}
    <details><summary>选择镜头与首帧 · 已选 {selected.length} 镜</summary><p>首帧需 240–8000 像素、PNG/JPEG/WebP、5 MB 以内。图生视频时以首帧决定画幅，请与制作包一致。</p>
      {shots.map((s:any)=><div className="video-shot-picker" key={s.id}><label><input type="checkbox" disabled={busy||inFlight(s.id)} checked={selected.includes(s.id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,s.id]:ids.filter(id=>id!==s.id))}/>{s.id} · {s.beat}</label>{packageData.options.mode==='image'&&<label>上传本镜首帧<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>upload(s.id,e.target.files?.[0])}/></label>}{frames[s.id]&&<img src={frames[s.id]} alt={`${s.id} 首帧预览`}/ >}{inFlight(s.id)&&<p>已有待处理任务，请先查看下方状态。</p>}</div>)}
    </details>
    <div className="video-actions"><button className="video-primary" disabled={busy||blocked||!selected.length||!key.trim()} onClick={submit}>提交选中 {selected.length} 镜到视频服务（可能计费）</button><button disabled={busy||!key.trim()} onClick={refresh}>刷新生成进度</button></div>
    {status&&<p role="status">{status}</p>}{error&&<p className="video-error" role="alert">{error}</p>}
    <details open={jobs.length>0}><summary>生成记录 · {jobs.length}</summary>{jobs.map(job=><article key={job.id}><strong>{job.shot_id} · {labels[job.status]||job.status}</strong><p>{job.duration_seconds} 秒 · {new Date(job.created_at*1000).toLocaleString()}</p><p>任务编号：{job.provider_task_id||job.id}</p>{job.error&&<p role="alert">{systemMessage(job.error)}</p>}{['submitting','submission_unknown'].includes(job.status)&&<div><p>请到服务商控制台核对本次提交。系统不会自动重发；如已创建，可粘贴任务编号恢复查询。</p><label>服务商任务编号<input value={recoverIds[job.id]||''} onChange={e=>setRecoverIds(ids=>({...ids,[job.id]:e.target.value}))}/></label><button disabled={busy||!recoverIds[job.id]||!keys[job.config.provider]} onClick={()=>recover(job)}>恢复已有任务（不重新生成）</button></div>}{job.source_hash&&job.source_hash!==packageData.source_hash&&<p>此视频来自较早的剧本版本。</p>}{job.status==='completed'&&<div className="video-actions">{!job.archived?<button disabled={busy} onClick={()=>archive(job)}>保存到项目并预览</button>:<><button onClick={()=>setPreview(job.id)}>预览</button><a href={`${base}/jobs/${job.id}/file?download=1`}>下载 MP4</a></>}{job.kind!=='assembly'&&<label><input type="checkbox" checked={editOrder.includes(job.id)} onChange={e=>setEditOrder(ids=>e.target.checked?[...ids,job.id]:ids.filter(id=>id!==job.id))}/>加入成片</label>}</div>}</article>)}</details>
    {preview&&<video className="video-player" key={preview} src={`${base}/jobs/${preview}/file`} controls preload="metadata"/>}
    {!!editOrder.length&&<div><h3>成片镜头顺序</h3>{editOrder.map((id,i)=><div className="video-actions" key={id}><span>{i+1}. {jobs.find(j=>j.id===id)?.shot_id}</span><button disabled={i===0||busy} onClick={()=>move(i,-1)}>上移</button><button disabled={i===editOrder.length-1||busy} onClick={()=>move(i,1)}>下移</button></div>)}<p>统一到 720p / 24fps，保留已有音轨；无声镜头保持静音。合成不调用模型。</p><button disabled={busy||!catalog?.can_assemble} onClick={assemble}>用 FFmpeg 合成所选镜头</button>{!catalog?.can_assemble&&<p>服务器未配置 FFmpeg，暂不能合成。</p>}</div>}
  </section>;
}
