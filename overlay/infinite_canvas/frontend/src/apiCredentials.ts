import providerCatalog from './modelProviders.json';
import axios from 'axios';

export type ModelProvider = keyof typeof providerCatalog;
let currentProvider: ModelProvider = 'qwen';
let currentModel = 'qwen3.8-flash';
let hasCredential = false;
let hasQwenCredential = false;
let qwenWorkspaceId = '';

export function modelCredentialStatus(): { provider: ModelProvider; active: boolean; model: string } {
  return { provider: currentProvider, active: hasCredential, model: currentModel };
}

export function qwenCredentialStatus(): boolean {
  return hasQwenCredential || (currentProvider === 'qwen' && hasCredential);
}

export function setModelCredential(provider: ModelProvider, key: string, model?: string): void {
  currentProvider = provider;
  currentModel = model || providerCatalog[provider].models[0].id;
  hasCredential = Boolean(key.trim());
  if (key.trim()) {
    axios.defaults.headers.common['X-Morpho-Model'] = currentModel;
    axios.defaults.headers.common['X-Morpho-Provider'] = provider;
    axios.defaults.headers.common['X-Morpho-Api-Key'] = key.trim();
  } else {
    delete axios.defaults.headers.common['X-Morpho-Model'];
    delete axios.defaults.headers.common['X-Morpho-Provider'];
    delete axios.defaults.headers.common['X-Morpho-Api-Key'];
  }
}

export function setQwenCredential(key: string): void {
  hasQwenCredential = Boolean(key.trim());
  if (hasQwenCredential) {
    axios.defaults.headers.common['X-Morpho-Qwen-Api-Key'] = key.trim();
  } else {
    delete axios.defaults.headers.common['X-Morpho-Qwen-Api-Key'];
  }
}

export function setQwenWorkspaceId(value: string): void {
  qwenWorkspaceId = value.trim();
  if (qwenWorkspaceId) {
    axios.defaults.headers.common['X-Morpho-Qwen-Workspace-Id'] = qwenWorkspaceId;
  } else {
    delete axios.defaults.headers.common['X-Morpho-Qwen-Workspace-Id'];
  }
}


export function thinkingModeStatus():boolean { return axios.defaults.headers.common['X-Morpho-Thinking']==='on'; }

export function setThinkingMode(enabled:boolean):void {
  axios.defaults.headers.common['X-Morpho-Thinking']=enabled?'on':'off';
}
