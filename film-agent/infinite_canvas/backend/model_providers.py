"""Explicit provider protocols and model-specific capability contracts."""
import json,re
from pathlib import Path
CATALOG=json.loads(Path(__file__).with_name('model_catalog.json').read_text())

def model_config(provider, model=None):
    service=CATALOG.get(provider)
    if not service: raise ValueError('请选择受支持的 API 服务商。')
    selected=model or service['models'][0]['id']
    for entry in service['models']:
        if entry['id']==selected:return service,entry
    raise ValueError('所选模型不属于该服务商，请重新选择。')

def prepare_request(credential,prompt,system,temperature,max_tokens,json_response,thinking,requested_model=None):
    provider=credential['provider']
    selected=credential.get('model')
    if not selected and provider=='qwen':selected=requested_model
    service,model=model_config(provider,selected)
    mode=model['thinking'];pref=credential.get('thinking')
    enabled=(pref=='on') if pref in {'on','off'} else bool(thinking)
    if mode=='always':enabled=True
    if mode=='none':enabled=False
    limit=min(max_tokens or 16000,32000)
    headers={'Authorization':'Bearer '+credential['key'],'Content-Type':'application/json'}
    system=(system or '')+ ('\n只返回完整JSON对象，不添加说明或代码围栏。' if json_response else '')
    protocol=service.get('protocol','openai')
    messages=([{'role':'system','content':system}] if system else [])+[{'role':'user','content':prompt}]
    payload={'model':model['id'],'messages':messages,'stream':False,'max_tokens':limit,'temperature':temperature}
    if mode=='enable' and (pref in {'on','off'} or thinking is not None):payload['enable_thinking']=enabled
    if mode=='effort':
        payload['reasoning_effort']='medium' if enabled else 'none'
        payload['max_completion_tokens']=payload.pop('max_tokens')
        if enabled:payload.pop('temperature',None)
    if mode=='type':payload['thinking']={'type':'enabled' if enabled else 'disabled'}
    if provider in {'qwen','deepseek'} and enabled and model['id'].startswith('qwen'):
        payload['thinking_budget']=1024
    if provider=='moonshot':payload['temperature']=1 if enabled else .6
    if json_response and not enabled:payload['response_format']={'type':'json_object'}
    if protocol=='anthropic':
        headers={'x-api-key':credential['key'],'anthropic-version':'2023-06-01','Content-Type':'application/json'}
        payload={'model':model['id'],'messages':[{'role':'user','content':prompt}],'max_tokens':max(limit,2048),'system':system}
        if enabled:payload['thinking']={'type':'enabled','budget_tokens':1024}
        else:payload['temperature']=temperature
    elif protocol=='gemini':
        headers={'x-goog-api-key':credential['key'],'Content-Type':'application/json'}
        config={'temperature':temperature,'maxOutputTokens':max(limit,2048),'thinkingConfig':{'thinkingBudget':1024 if enabled else 0}}
        if json_response:config['responseMimeType']='application/json'
        payload={'contents':[{'role':'user','parts':[{'text':prompt}]}],'generationConfig':config}
        if system:payload['systemInstruction']={'parts':[{'text':system}]}
    if max_tokens is None and protocol=='openai':
        payload.pop('max_tokens',None);payload.pop('max_completion_tokens',None)
    return service['endpoint'].format(model=model['id']),payload,headers,protocol

def parse_response(data,protocol):
    if protocol=='anthropic':
        return ''.join(p.get('text','') for p in data['content'] if p.get('type')=='text'), ('length' if data.get('stop_reason')=='max_tokens' else 'stop')
    if protocol=='gemini':
        item=data['candidates'][0]
        return ''.join(p.get('text','') for p in item['content']['parts'] if not p.get('thought')), ('length' if item.get('finishReason')=='MAX_TOKENS' else 'stop')
    choice=data['choices'][0];content=choice['message'].get('content')
    if isinstance(content,list):content=''.join(p.get('text','') for p in content if isinstance(p,dict))
    if isinstance(content,str):content=re.sub(r'<think>.*?</think>','',content,flags=re.S)
    return content,choice.get('finish_reason')
