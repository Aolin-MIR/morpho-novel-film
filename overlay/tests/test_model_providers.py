import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infinite_canvas/backend'))
from model_providers import CATALOG,prepare_request,parse_response

@pytest.mark.parametrize('provider,model',[(p,m) for p,v in CATALOG.items() for m in v['models']])
@pytest.mark.parametrize('thinking',['on','off'])
def test_provider_payload_contract(provider,model,thinking):
    cred={'provider':provider,'key':'test-secret','model':model['id'],'thinking':thinking}
    url,body,headers,protocol=prepare_request(cred,'Return JSON','System',.2,4000,True,None)
    assert url.startswith('https://')
    assert body and headers
    if protocol=='anthropic':
        assert headers['x-api-key']=='test-secret'
        assert 'response_format' not in body
        assert (body.get('thinking',{}).get('type')=='enabled')==(thinking=='on')
    elif protocol=='gemini':
        assert headers['x-goog-api-key']=='test-secret'
        assert body['generationConfig']['thinkingConfig']['thinkingBudget']==(1024 if thinking=='on' else 0)
    else:
        assert headers['Authorization']=='Bearer test-secret'
        if model['thinking']=='enable': assert body['enable_thinking']==(thinking=='on')
        if model['thinking']=='type': assert body['thinking']['type']==('enabled' if thinking=='on' else 'disabled')
        if model['thinking']=='none': assert 'enable_thinking' not in body and 'thinking' not in body
        if model['thinking']=='effort':
            assert body['reasoning_effort']==('medium' if thinking=='on' else 'none')
            assert 'max_tokens' not in body
            assert ('temperature' in body)==(thinking=='off')

def test_native_response_text_excludes_thinking():
    assert parse_response({'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':'story'}],'stop_reason':'end_turn'},'anthropic')==('story','stop')
    assert parse_response({'candidates':[{'content':{'parts':[{'thought':True,'text':'private'},{'text':'story'}]},'finishReason':'MAX_TOKENS'}]},'gemini')==('story','length')


def test_frontend_and_backend_catalog_are_identical():
    import json
    ui=Path(__file__).resolve().parents[1]/'infinite_canvas/frontend/src/modelProviders.json'
    assert json.loads(ui.read_text())==CATALOG
