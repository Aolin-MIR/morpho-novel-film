import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { API_BASE } from '../apiBase';
import { systemMessage } from '../utils/systemMessage';

const base = `${API_BASE}/video-workflow`;

type FilmJob = {
  id: string;
  kind: string;
  config?: { provider: string };
  status: string;
  stage: string;
  shot_id: string;
  error?: string;
  duration_seconds?: number;
  completed_shots: number;
  package_created_at: string;
  reference_assets?: { name: string; file: string }[];
  active_take?: { provider_task_id?: string };
  review_exhausted?: boolean;
  edit_project?: string;
};

export default function NovelFilmRender({ createdAt, editedAt, shotCount, blocked }: {
  createdAt?: string;
  editedAt?: string;
  shotCount?: number;
  blocked: boolean;
}) {
  const [key, setKey] = useState('');
  const [target, setTarget] = useState('api');
  const [rental, setRental] = useState<{ configured: boolean; ready: boolean; active: number; pending_downloads: number }>();
  const [rate, setRate] = useState('');
  const [coldMinutes, setColdMinutes] = useState('12');
  const [batchMinutes, setBatchMinutes] = useState('');
  const [pilot, setPilot] = useState('');
  const [jobs, setJobs] = useState<FilmJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState('');
  const requestId = useRef('');

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const { data } = await axios.get<{ jobs: FilmJob[]; rental_configured: boolean }>(`${base}/jobs`, { signal: controller.signal });
        setJobs(data.jobs.filter(job => job.kind === 'film'));
        if (data.rental_configured) {
          const status = await axios.get(`${base}/rental-status`, { signal: controller.signal });
          setRental(status.data);
        }
        setLoading(false);
      } catch {
        if (!controller.signal.aborted) setError('暂时无法读取影片制作进度。');
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  const active = jobs.some(job => ['queued', 'running', 'submission_unknown', 'art_ready', 'pilot_ready'].includes(job.status));
  const submit = async () => {
    setBusy(true);
    setError('');
    if (!requestId.current) requestId.current = crypto.randomUUID();
    try {
      const { data } = await axios.post<FilmJob>(`${base}/films`, {
        request_id: requestId.current,
        target,
        package_created_at: createdAt,
        package_edited_at: editedAt,
      }, { headers: { 'X-Video-Api-Key': key.trim() }, timeout: 15000 });
      setJobs(previous => [data, ...previous.filter(job => job.id !== data.id)]);
      requestId.current = '';
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) requestId.current = '';
      setError(systemMessage(axios.isAxiosError(err) ? err.response?.data?.error || err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const resume = async (id: string) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await axios.post<FilmJob>(`${base}/films/${id}/resume`, {}, {
        headers: { 'X-Video-Api-Key': key.trim() }, timeout: 15000,
      });
      setJobs(previous => previous.map(job => job.id === id ? data : job));
    } catch (err) {
      setError(systemMessage(axios.isAxiosError(err) ? err.response?.data?.error || err.message : err));
    } finally {
      setBusy(false);
    }
  };

  const close = async (id: string) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await axios.post<FilmJob>(`${base}/films/${id}/close`, { confirmed: true });
      setJobs(previous => previous.map(job => job.id === id ? data : job));
    } catch (err) {
      setError(systemMessage(axios.isAxiosError(err) ? err.response?.data?.error || err.message : err));
    } finally {
      setBusy(false);
    }
  };

  return <section className="video-execution">
    <h3>影片制作进度</h3>
    <label>视频渲染
      <select value={target} onChange={event => setTarget(event.target.value)} disabled={active || busy}>
        <option value="api">MiniMax 云服务</option>
        {rental?.configured && <option value="ssh">租用的 H3 服务器</option>}
      </select>
    </label>
    {rental?.configured && <details open={target === 'ssh'}>
      <summary>算家云状态</summary>
      <p role="status">{rental.ready ? 'H3 已就绪' : '尚未连通 H3'}
        {rental.active > 0 ? ` · ${rental.active} 个制作任务 · ${rental.pending_downloads} 个镜头待确认回传` : ' · 当前项目无在途镜头'}</p>
      <div className="video-options__grid">
        <label>整组显卡每小时费用（元）<input type="number" min="0" step="0.01" value={rate}
          onChange={event => setRate(event.target.value)} /></label>
        <label>预计冷启动（分钟）<input type="number" min="0" step="1" value={coldMinutes}
          onChange={event => setColdMinutes(event.target.value)} /></label>
        <label>预计本批渲染与回传（分钟）<input type="number" min="0" step="1" value={batchMinutes}
          onChange={event => setBatchMinutes(event.target.value)} /></label>
      </div>
      {rate !== '' && Number(rate) >= 0 && Number(coldMinutes) >= 0 && Number(batchMinutes) >= 0 && <p>
        一次冷启动约 ¥{(Number(rate) * Number(coldMinutes) / 60).toFixed(2)}
        {batchMinutes !== '' && `，本批预计 ¥${(Number(rate) * (Number(coldMinutes) + Number(batchMinutes)) / 60).toFixed(2)}`}
      </p>}
    </details>}
    <label>MiniMax 密钥
      <input type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} />
    </label>
    {createdAt && <p>{shotCount} 镜。自动检查参考图和试拍，试拍通过后继续制作。</p>}
    {createdAt && <button type="button" className="video-primary" onClick={submit} disabled={blocked || loading || busy || active || !key.trim()}>
      开始制作
    </button>}
    {error && <p className="video-error" role="alert">{error}</p>}
    {jobs.map(job => <article key={job.id}>
      <strong>{job.shot_id}</strong>
      <p role="status">{job.stage}{job.status === 'completed' && ` · ${job.duration_seconds} 秒`}</p>
      {job.error && <p role="alert">{systemMessage(job.error)}</p>}
      {job.status === 'submission_unknown' && <>
        <p>请在视频服务核对镜头是否仍在生成{job.active_take?.provider_task_id && `（${job.active_take.provider_task_id}）`}。</p>
        <button type="button" disabled={busy} onClick={() => close(job.id)}>已核对，结束本次制作</button>
      </>}
      {!!job.reference_assets?.length && <details>
        <summary>参考图</summary>
        <div className="video-options__grid">
          {job.reference_assets.map(asset => <figure key={asset.file}>
            <img loading="lazy" style={{ maxWidth: '100%' }}
              src={`${base}/films/${job.id}/art/${asset.file}`} alt={asset.name} />
            <figcaption>{asset.name}</figcaption>
          </figure>)}
        </div>
      </details>}
      {job.completed_shots > 0 && job.config?.provider !== 'openfilm_code' && <button type="button" onClick={() => setPilot(job.id)}>播放试拍</button>}
      {pilot === job.id && <video className="video-player" src={`${base}/films/${job.id}/preview`} controls preload="metadata" />}
      {((job.status === 'failed' && !job.review_exhausted) || (job.status === 'submission_unknown' && job.active_take?.provider_task_id)) &&
        <button type="button" disabled={busy || active && job.status === 'failed' || !key.trim()}
          onClick={() => resume(job.id)}>继续制作</button>}
      {job.status === 'completed' && <div className="video-actions">
        <button type="button" onClick={() => setPreview(job.id)}>播放短片</button>
        <a href={`${base}/jobs/${job.id}/file?download=1`}>下载短片</a>
        {job.edit_project && <a href={`${base}/films/${job.id}/edit-project`}>下载剪辑工程</a>}
      </div>}
    </article>)}
    {preview && <video className="video-player" key={preview} src={`${base}/jobs/${preview}/file`} controls preload="metadata" />}
  </section>;
}
