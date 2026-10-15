"""ChatGPT plan inference through the public Responses API, as a LangChain chat model.

The existing LangChain agent, tool budgets, write gate and citation checks stay in
charge; this adapter only performs model turns. Requests use the official OpenAI Python
SDK (`max_retries=0`, fixed base URL, an httpx client without environment proxies or
redirects) and follow the documented preview contract: `store: false`, `stream: true`,
the full context as an `input` array, `instructions` instead of system items, and none
of the unsupported fields (`max_output_tokens`, `temperature`, `previous_response_id`,
...). App-side budgets bound request size, streamed bytes, output text and time instead.
Success is only a `response.completed` event whose response status is `completed`;
anything else is an error and is never retried.
"""
import json
import re
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult

from .chatgpt import RESOURCE, USAGE_URL, ChatGPTAccounts, ChatGPTError

# Function tools are grouped in one namespace, as the preview requires. Shape per the
# OpenAI SDK's NamespaceToolParam: {type: "namespace", name, description, tools: [function]};
# returned function_call items carry `namespace` and the plain tool `name` separately.
NAMESPACE = 'mcdxkit'
MAX_STREAM_BYTES = 4 * 1024 * 1024      # all streamed events, including the terminal response

_RECOVERY = {
    'subscription_sharing_user_not_eligible': "ChatGPT plan use isn't available for this account, workspace or policy.",
    'subscription_sharing_usage_limit_exceeded': f'ChatGPT plan usage limit reached for MCDXKit. Manage usage: {USAGE_URL}',
    'subscription_sharing_usage_unavailable': 'ChatGPT could not check plan usage right now. Try again later.',
    'subscription_sharing_unsupported_capability': 'ChatGPT rejected a request feature as unsupported for plan usage.',
    'subscription_sharing_route_not_supported': 'ChatGPT does not support this request route for plan usage.',
    'subscription_sharing_invalid_user': 'ChatGPT could not validate the signed-in account. Sign in again from Services.',
    'chatpass_v2_scope_not_authorized': 'This ChatGPT sign-in does not authorize plan usage. Check the account in Services.',
    'chatpass_v2_invalid_authorization_context': 'This ChatGPT sign-in does not authorize plan usage. Check the account in Services.',
    'subscription_sharing_user_unavailable': 'ChatGPT account information is temporarily unavailable. Try again later.',
}
_ADMISSION = {401: 'ChatGPT did not accept the sign-in or its permission. Check the account in Services.',
              403: 'A ChatGPT policy or permission check (for example the serving region) prevented this request.',
              503: 'ChatGPT plan routing is temporarily unavailable. Try again later.'}
_SUFFIX = ' Nothing was accepted, no other provider was used and the request was not retried.'


def _text(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        return ''.join(part if isinstance(part, str) else part.get('text', '') for part in content
                       if isinstance(part, str) or (isinstance(part, dict) and part.get('type') in ('text', 'output_text', 'input_text')))
    return ''


def _safe(value, pattern=r'[A-Za-z0-9_.:-]{1,120}'):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else ''


def _failure(error, request_id='', status=None):
    error = error if isinstance(error, dict) else {}
    code, param = _safe(error.get('code')), _safe(error.get('param'), r'[A-Za-z0-9_.\[\]:-]{1,120}')
    message = _RECOVERY.get(code) or (_ADMISSION.get(status) if status else None) or 'ChatGPT could not complete the request.'
    details = ', '.join(x for x in (f'HTTP {status}' if status else '', f'code {code}' if code else '',
                                    f'param {param}' if param else '', f'request {request_id}' if request_id else '') if x)
    return ChatGPTError(message + (f' ({details}).' if details else '') + _SUFFIX)


def _function_tool(tool):
    from langchain_core.utils.function_calling import convert_to_openai_tool
    spec = convert_to_openai_tool(tool)['function']
    return {'type': 'function', 'name': spec['name'], 'description': spec.get('description', ''),
            'parameters': spec.get('parameters', {'type': 'object', 'properties': {}})}


def output_text(response):
    return ''.join(part.get('text', '') for item in response.get('output') or [] if isinstance(item, dict) and item.get('type') == 'message'
                   for part in item.get('content') or [] if isinstance(part, dict) and part.get('type') == 'output_text')


class ChatGPTResponses(BaseChatModel):
    """One Responses request per model turn using the selected ChatGPT account."""
    model: str
    workspace: str
    account: str
    deadline_seconds: float = 180.0     # checked per event; a stalled read adds at most the 60 s read timeout
    max_output_chars: int = 200_000
    max_request_bytes: int = 2_000_000

    @property
    def _llm_type(self):
        return 'chatgpt-responses'

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        # Only tool definitions and choice are forwarded; other provider settings are unsupported here.
        choice = None if tool_choice in (None, 'auto') else 'none' if tool_choice == 'none' else 'required'
        return self.bind(tools=[_function_tool(t) for t in tools], tool_choice=choice)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, tools=None, tool_choice=None, **kwargs: Any):
        instructions: list[str] = []
        items: list[dict] = []
        for message in messages:
            if isinstance(message, SystemMessage): instructions.append(_text(message.content))
            elif isinstance(message, HumanMessage): items.append({'role': 'user', 'content': _text(message.content)})
            elif isinstance(message, ToolMessage):
                items.append({'type': 'function_call_output', 'call_id': message.tool_call_id, 'output': _text(message.content)})
            elif isinstance(message, AIMessage):
                raw = message.additional_kwargs.get('responses_items')
                if isinstance(raw, list): items.extend(raw)
                else:
                    if _text(message.content): items.append({'role': 'assistant', 'content': _text(message.content)})
                    items.extend({'type': 'function_call', 'call_id': call['id'], 'name': call['name'], 'namespace': NAMESPACE,
                                  'arguments': json.dumps(call['args'])} for call in message.tool_calls)
            else:
                raise ChatGPTError('Unsupported conversation message for ChatGPT.')
        body: dict[str, Any] = {'model': self.model, 'input': items, 'store': False, 'stream': True}
        if instructions: body['instructions'] = '\n\n'.join(instructions)
        allowed = {tool['name'] for tool in tools or []}
        if tools:
            body['tools'] = [{'type': 'namespace', 'name': NAMESPACE, 'description': 'MCDXKit project document tools.', 'tools': tools}]
            if tool_choice: body['tool_choice'] = tool_choice
        response = self.complete(body)
        calls: list[dict] = []
        invalid: list[dict] = []
        replay: list[dict] = []
        text = output_text(response)
        if text: replay.append({'role': 'assistant', 'content': text})
        for item in response.get('output') or []:
            if not isinstance(item, dict) or item.get('type') != 'function_call': continue
            name, call_id = item.get('name'), item.get('call_id')
            problem = None
            # Only calls to a tool offered in this request, in our namespace, can execute.
            if item.get('namespace') != NAMESPACE or name not in allowed or not isinstance(call_id, str) or not call_id:
                problem = 'Not an available MCDXKit tool; it was not executed.'
            else:
                try:
                    args = json.loads(item.get('arguments') or '{}')
                    if not isinstance(args, dict): raise ValueError()
                except ValueError:
                    problem = 'Tool arguments were not valid JSON; it was not executed.'
            if problem:
                invalid.append({'name': _safe(name, r'[A-Za-z0-9_.-]{1,80}'), 'args': None, 'id': _safe(call_id) or None,
                                'error': problem, 'type': 'invalid_tool_call'})
                continue
            calls.append({'name': name, 'args': args, 'id': call_id, 'type': 'tool_call'})
            # Replayed as history (store is false, so nothing is referenced by response or item ID).
            replay.append({'type': 'function_call', 'call_id': call_id, 'name': name, 'namespace': NAMESPACE,
                           'arguments': item.get('arguments') or '{}'})
        usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
        metadata: UsageMetadata | None = None
        if all(type(usage.get(k)) is int for k in ('input_tokens', 'output_tokens')):
            metadata = {'input_tokens': usage['input_tokens'], 'output_tokens': usage['output_tokens'],
                        'total_tokens': usage['input_tokens'] + usage['output_tokens']}
        message = AIMessage(content=text, tool_calls=calls, invalid_tool_calls=invalid,
                            additional_kwargs={'responses_items': replay}, usage_metadata=metadata,
                            response_metadata={'model_name': _safe(response.get('model')), 'provider': 'chatgpt'})
        return ChatResult(generations=[ChatGeneration(message=message)])

    def complete(self, body):
        """Create a streamed response with the OpenAI SDK and read it to its terminal event."""
        import httpx
        from openai import APIConnectionError, APIError, APIStatusError, OpenAI
        if len(json.dumps(body, allow_nan=False).encode()) > self.max_request_bytes:
            raise ChatGPTError("This conversation exceeds MCDXKit's request budget for ChatGPT. Narrow the question." + _SUFFIX)
        if body.get('stream') is not True or body.get('store') is not False:
            raise ChatGPTError('Internal error: ChatGPT plan requests must stream and must not be stored.')
        token = ChatGPTAccounts(self.workspace).access_token(self.account)     # plan scope checked here
        started, received, produced = time.monotonic(), 0, 0
        http = httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False, trust_env=False)
        client = OpenAI(api_key=token, base_url=RESOURCE, max_retries=0, http_client=http)
        try:
            with client.responses.create(**body) as stream:
                for event in stream:
                    data = event.model_dump(mode='json', exclude_none=True)
                    received += len(json.dumps(data))
                    if received > MAX_STREAM_BYTES:
                        raise ChatGPTError("ChatGPT's response exceeded MCDXKit's size budget." + _SUFFIX)
                    if time.monotonic() - started > self.deadline_seconds:
                        raise ChatGPTError("ChatGPT did not finish within MCDXKit's time budget." + _SUFFIX)
                    kind = data.get('type')
                    if kind in ('response.output_text.delta', 'response.function_call_arguments.delta'):
                        produced += len(data.get('delta') or '')
                        if produced > self.max_output_chars:
                            raise ChatGPTError("ChatGPT's response exceeded MCDXKit's output budget." + _SUFFIX)
                    elif kind == 'response.completed':
                        response = data.get('response') if isinstance(data.get('response'), dict) else {}
                        if response.get('status') != 'completed':
                            raise ChatGPTError('ChatGPT ended without a completed response.' + _SUFFIX)
                        if len(output_text(response)) > self.max_output_chars:
                            raise ChatGPTError("ChatGPT's response exceeded MCDXKit's output budget." + _SUFFIX)
                        return response
                    elif kind == 'response.failed':
                        failed = data.get('response') if isinstance(data.get('response'), dict) else {}
                        raise _failure(failed.get('error'))
                    elif kind == 'response.incomplete':
                        details = (data.get('response') or {}).get('incomplete_details') or {}
                        reason = _safe(details.get('reason') if isinstance(details, dict) else None)
                        raise ChatGPTError(f'ChatGPT stopped before completing the response{" (" + reason + ")" if reason else ""}.' + _SUFFIX)
                    elif kind == 'error':
                        raise _failure(data)
        except APIStatusError as exc:
            raise _failure(exc.body, _safe(exc.request_id), exc.status_code) from None
        except (APIConnectionError, httpx.HTTPError):
            raise ChatGPTError('The ChatGPT connection was interrupted before the response completed.' + _SUFFIX) from None
        except APIError as exc:
            raise _failure(exc.body) from None
        finally:
            http.close()
        raise ChatGPTError('The ChatGPT stream ended before the response completed.' + _SUFFIX)


class ChatGPTExtractor:
    """Ask's requirement extraction on the selected ChatGPT model, same schema and checks as DeepSeek."""

    def __init__(self, workspace, account, model):
        self.model = 'chatgpt:' + model
        self._chat = ChatGPTResponses(model=model, workspace=str(workspace), account=account)

    def extract(self, context):
        from .extraction import INSTRUCTION, extraction_schema
        started = time.monotonic()
        response = self._chat.complete({
            'model': self._chat.model, 'instructions': INSTRUCTION, 'store': False, 'stream': True,
            'input': [{'role': 'user', 'content': json.dumps(context)}],
            'text': {'format': {'type': 'json_schema', 'name': 'source_requirements', 'strict': True, 'schema': extraction_schema()}}})
        try: payload = json.loads(output_text(response))
        except ValueError: raise ValueError('ChatGPT returned invalid extraction JSON; no results saved.') from None
        usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
        return payload, {'model': _safe(response.get('model')) or self._chat.model, 'provider': 'chatgpt',
                         'elapsed_seconds': round(time.monotonic() - started, 2),
                         'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens') if type(usage.get(k)) is int}}
