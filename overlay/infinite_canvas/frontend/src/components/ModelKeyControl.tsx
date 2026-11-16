import providerCatalog from '../modelProviders.json';
import { Locale, resolveInitialLocale } from '../studioLocale';
import { entryText, entryMessage } from '../entryLocale';
import React, { useId, useState } from 'react';
import axios from 'axios';
import { ModelProvider, setThinkingMode, thinkingModeStatus, modelCredentialStatus, qwenCredentialStatus, setModelCredential, setQwenCredential, setQwenWorkspaceId } from '../apiCredentials';
import { API_BASE } from '../apiBase';

export default function ModelKeyControl({ locale = resolveInitialLocale() }: { locale?: Locale }) {
  const t = (text: string) => entryText(locale, text);
  const controlId = useId();
  const secure = typeof window === 'undefined' || window.location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const [provider, setProvider] = useState<ModelProvider>(() => modelCredentialStatus().provider);
  const [thinking,setThinking]=useState(thinkingModeStatus);
  const [model,setModel]=useState(()=>modelCredentialStatus().model);
  const [key, setKey] = useState('');
  const [active, setActive] = useState(() => modelCredentialStatus().active);
  const [qwenKey, setQwenKey] = useState('');
  const [qwenActive, setQwenActive] = useState(() => qwenCredentialStatus());
  const [workspaceId, setWorkspaceId] = useState('');
  const [checkStatus, setCheckStatus] = useState('');
  const [checking, setChecking] = useState(false);
  const choices=providerCatalog[provider].models;
  const capability=choices.find(item=>item.id===model)?.thinking||'none';
  const switchable=!['none','always'].includes(capability);
  const check = async () => {
    setChecking(true); setCheckStatus('');
    try {
      const response = await axios.post(`${API_BASE}/api/model/check`, {}, { timeout: 45000 });
      setCheckStatus(response.data?.ok ? t('连接正常') : t('连接失败'));
    } catch (reason: any) {
      setCheckStatus(entryMessage(locale, reason?.response?.data?.error || reason?.message || t('连接失败')));
    } finally { setChecking(false); }
  };
  return <div className="model-key-control">
    <label htmlFor={`${controlId}-model-provider`}>{t("API 服务商")}</label>
    <select id={`${controlId}-model-provider`} value={provider} onChange={event=>{const next=event.target.value as ModelProvider;setProvider(next);setModel(providerCatalog[next].models[0].id);setThinking(false);setThinkingMode(false);setKey('');setActive(false);setCheckStatus('');setModelCredential(next,'');}}>
      {Object.entries(providerCatalog).filter(([id])=>id!=='deepseek').map(([id,value])=><option key={id} value={id}>{t(value.label)}</option>)}
    </select>
    <label htmlFor={`${controlId}-model-choice`}>{t("选择模型")}</label>
    <select id={`${controlId}-model-choice`} value={model} onChange={event=>{const selected=event.target.value;setModel(selected);setThinking(false);setThinkingMode(false);setModelCredential(provider,'',selected);setActive(false);setCheckStatus(t('请填写此服务商的密钥并启用'));}}>
      {choices.map(item=><option key={item.id} value={item.id}>{t(item.label)} · {item.id}</option>)}
    </select>
    <label className="model-thinking-toggle"><input type="checkbox" checked={capability==='always'||(switchable&&thinking)} disabled={!switchable} onChange={event=>{setThinking(event.target.checked);setThinkingMode(event.target.checked);setCheckStatus(t('思考模式已更新'));}}/>{t("开启思考模式")}</label>
    {!switchable&&<small>{t(capability==='always'?'此模型始终使用思考模式。':'此模型不支持独立思考开关。')}</small>}
    <label htmlFor={`${controlId}-model-api-key`}>API Key</label>
    <input id={`${controlId}-model-api-key`} type="password" autoComplete="off" spellCheck={false} value={key} disabled={!secure}
      onChange={(event) => { setKey(event.target.value); setActive(false); setModelCredential(provider, ''); }}
      placeholder={t("输入密钥")} />
    <button type="button" onClick={() => { setThinkingMode(switchable&&thinking);setModelCredential(provider, key, model); setActive(Boolean(key.trim())); setQwenActive(qwenCredentialStatus()); setKey(''); setCheckStatus(''); }} disabled={!secure || !key.trim()}>{t("启用")}</button>
    <button type="button" onClick={() => { setModelCredential(provider, ''); setKey(''); setActive(false); }}>{t("清除")}</button>
    {active && <button type="button" onClick={() => void check()} disabled={checking}>{checking ? t('测试中…') : t('测试连接')}</button>}
    <small>{!secure ? <a href={`https://looloothetoilet.duckdns.org${window.location.pathname}${window.location.search}${window.location.hash}`}>{t("请进入 HTTPS 安全入口后添加密钥")}</a> : (checkStatus || (active ? t('已启用文本模型') : ''))}</small>
    {provider !== 'qwen' && <><label htmlFor={`${controlId}-qwen-capability-key`}>{t("Qwen 密钥（语音与图像，可选）")}</label>
      <input id={`${controlId}-qwen-capability-key`} type="password" autoComplete="off" spellCheck={false} value={qwenKey} disabled={!secure}
        onChange={(event) => setQwenKey(event.target.value)} placeholder={t("输入 Qwen 密钥")} />
      <button type="button" onClick={() => { setQwenCredential(qwenKey); setQwenActive(Boolean(qwenKey.trim())); setQwenKey(''); }} disabled={!secure || !qwenKey.trim()}>{t("启用")}</button>
      {qwenActive && <button type="button" onClick={() => { setQwenCredential(''); setQwenActive(false); }}>{t("清除 Qwen 密钥")}</button>}
      <small>{qwenActive ? t('已启用') : t('')}</small>
    </>}
    <label htmlFor={`${controlId}-qwen-workspace-id`}>{t("Qwen Workspace ID（音效、音乐、图像）")}</label>
    <input id={`${controlId}-qwen-workspace-id`} value={workspaceId} disabled={!secure} autoComplete="off" spellCheck={false}
      onChange={(event) => setWorkspaceId(event.target.value)} placeholder={t("开通音频模型后填写；其余能力可留空")} />
    <button type="button" onClick={() => { setQwenWorkspaceId(workspaceId); setCheckStatus(t('Workspace ID 已应用')); }} disabled={!secure}>{t("应用")}</button>
  </div>;
}
