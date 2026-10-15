"""Exercise the real OpenAI SDK through a controlled transport, never a live account."""
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from openai import OpenAI as RealOpenAI
from mcdxkit.chatgpt import ChatGPTError
from mcdxkit.chatgpt_responses import ChatGPTResponses, NAMESPACE


def completed(output=None, status='completed'):
    return {'type':'response.completed','response':{'id':'resp_test','object':'response','status':status,'model':'test-model',
        'output':output or [{'type':'message','id':'msg_test','role':'assistant','content':[{'type':'output_text','text':'Checked answer.'}]}],
        'usage':{'input_tokens':10,'output_tokens':4,'total_tokens':14}}}


class ResponsesContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.chat=ChatGPTResponses(model='test-model',workspace=self.tmp.name,account='0000000000000000')
        self.requests=[];self.events=[completed()];self.status=200;self.error={};self.sdk_options=[];self.script=None
        self.enterContext(patch('mcdxkit.chatgpt_responses.ChatGPTAccounts.access_token',return_value='fake-token'))
        self.enterContext(patch('openai.OpenAI',side_effect=self.sdk))

    def sdk(self, **kwargs):
        self.sdk_options.append(kwargs)
        kwargs['http_client'].close()
        return RealOpenAI(api_key='fake-token',max_retries=0,base_url='https://api.openai.com/v1',
                          http_client=httpx.Client(transport=httpx.MockTransport(self.respond)))

    def respond(self, request):
        self.requests.append(json.loads(request.content))
        if self.status!=200:return httpx.Response(self.status,json=self.error,headers={'x-request-id':'req_test'})
        events=self.script.pop(0) if self.script else self.events
        data=''.join('data: '+json.dumps(e)+'\n\n' for e in events)
        return httpx.Response(200,content=data.encode(),headers={'content-type':'text/event-stream'})

    def test_real_langchain_agent_uses_selected_chatgpt_provider_for_retrieval_and_answer(self):
        from mcdxkit.document_clients import ModelConfig
        from mcdxkit.knowledge import KnowledgeAgent
        from test_knowledge import answer
        def event(name,args):
            return completed([{'type':'function_call','name':name,'namespace':NAMESPACE,'call_id':'call_'+name,'arguments':json.dumps(args)}])
        self.script=[[event('search_documents',{'query':'wall settlement'})],[event('KnowledgeAnswer',answer())]]
        config=ModelConfig('','','','','test:free',provider='chatgpt',chatgpt_model='test-model')
        agent=KnowledgeAgent(config,workspace=self.tmp.name,chatgpt_account='0000000000000000')
        searches=[]
        def search(q):
            searches.append(q)
            return [{'id':'E1','source_name':'Public fixture','text':'The limit is 2 inches','positions':[[2,0,0,0,0]]}]
        result=agent.answer({'request':'What is the wall settlement limit?'},search)
        self.assertEqual(searches,['wall settlement'])
        self.assertEqual(result['status'],'supported')
        self.assertEqual(len(self.requests),2)
        self.assertEqual([r['model'] for r in self.requests],['test-model','test-model'])
        self.assertTrue(any(i.get('type')=='function_call_output' for i in self.requests[1]['input']))

    def test_uses_full_history_public_responses_contract_and_no_unsupported_fields(self):
        result=self.chat.invoke([SystemMessage(content='Instructions'),HumanMessage(content='Hello')])
        body=self.requests[0]
        self.assertFalse(body['store']);self.assertTrue(body['stream'])
        self.assertEqual(body['instructions'],'Instructions')
        self.assertEqual(body['input'],[{'role':'user','content':'Hello'}])
        for key in ('temperature','max_output_tokens','previous_response_id','metadata','top_p'):
            self.assertNotIn(key,body)
        self.assertEqual(result.content,'Checked answer.')
        self.assertEqual(self.sdk_options[0]['max_retries'],0)

    def test_tool_namespace_and_results_replay_in_followup(self):
        tool={'type':'function','function':{'name':'search_documents','description':'Search project','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}}
        self.events=[completed([{'type':'function_call','name':'search_documents','namespace':NAMESPACE,'call_id':'call_test','arguments':'{"query":"limit"}'}])]
        first=self.chat.bind_tools([tool]).invoke([HumanMessage(content='Find the limit')])
        self.assertEqual(first.tool_calls[0]['name'],'search_documents')
        self.assertEqual(self.requests[0]['tools'][0]['type'],'namespace')
        self.assertEqual(self.requests[0]['tools'][0]['name'],NAMESPACE)
        self.events=[completed()]
        self.chat.bind_tools([tool]).invoke([HumanMessage(content='Find the limit'),first,ToolMessage(content='Evidence text',tool_call_id='call_test')])
        items=self.requests[1]['input']
        self.assertTrue(any(i.get('type')=='function_call' and i.get('call_id')=='call_test' for i in items))
        self.assertTrue(any(i.get('type')=='function_call_output' and i.get('output')=='Evidence text' for i in items))

    def test_unregistered_tools_foreign_namespace_and_invalid_args_never_execute(self):
        tool={'type':'function','function':{'name':'allowed','parameters':{'type':'object','properties':{}}}}
        for call in [
            {'name':'shell','namespace':NAMESPACE,'arguments':'{}'},
            {'name':'allowed','namespace':'foreign','arguments':'{}'},
            {'name':'allowed','namespace':NAMESPACE,'arguments':'[]'},
            {'name':'allowed','namespace':NAMESPACE,'arguments':'not json'}]:
            with self.subTest(call=call):
                self.events=[completed([{'type':'function_call','call_id':'test_call',**call}])]
                result=self.chat.bind_tools([tool]).invoke([HumanMessage(content='Hi')])
                self.assertEqual(result.tool_calls,[])
                self.assertTrue(result.invalid_tool_calls)

    def test_partial_deltas_then_error_are_not_success_and_never_retry(self):
        self.events=[{'type':'response.output_text.delta','delta':'provisional text'},
                     {'type':'response.failed','response':{'status':'failed','error':{'code':'subscription_sharing_usage_limit_exceeded'}}}]
        with self.assertRaisesRegex(ChatGPTError,'usage limit'):
            self.chat.invoke([HumanMessage(content='Hi')])
        self.assertEqual(len(self.requests),1)

    def test_eof_incomplete_and_false_completion_fail_closed(self):
        for events in [[],[{'type':'response.incomplete','response':{'status':'incomplete','incomplete_details':{'reason':'budget'}}}], [completed(status='failed')]]:
            with self.subTest(events=events):
                self.events=events
                with self.assertRaises(ChatGPTError):self.chat.invoke([HumanMessage(content='Hi')])

    def test_http_error_hides_provider_echo_and_does_not_retry(self):
        self.status=403;self.error={'error':{'code':'subscription_sharing_user_not_eligible','message':'fake-token private source text'}}
        with self.assertRaises(ChatGPTError) as caught:self.chat.invoke([HumanMessage(content='Hi')])
        self.assertNotIn('fake-token',str(caught.exception));self.assertNotIn('private source text',str(caught.exception))
        self.assertEqual(len(self.requests),1)

    def test_terminal_text_exceeding_limit_without_deltas_is_rejected(self):
        self.chat.max_output_chars=4
        with self.assertRaises(ChatGPTError):self.chat.invoke([HumanMessage(content='Hi')])

    def test_request_budget_rejects_before_transmission(self):
        self.chat.max_request_bytes=20
        with self.assertRaises(ChatGPTError):self.chat.invoke([HumanMessage(content='More than twenty characters of request')])
        self.assertEqual(self.requests,[])


if __name__=='__main__':unittest.main()
