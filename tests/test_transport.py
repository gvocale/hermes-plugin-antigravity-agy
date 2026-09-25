import asyncio
import json
import os
import time

import pytest

import transport
from transport import (AgyAgentNotLoaded, AgyError, AgyLoggedOut, AgyMissing, AgyNativeToolViolation,
                       AgyQuotaExhausted, Client, prepare_conversation)

TOOLS = [
    {'type': 'function', 'function': {'name': 'terminal', 'description': 'Run a shell command.', 'parameters': {
        'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}},
    {'type': 'function', 'function': {'name': 'read_file', 'description': 'Read a file.', 'parameters': {
        'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}},
]
USER = [{'role': 'system', 'content': 'You are Hermes.'}, {'role': 'user', 'content': 'hi'}]


def client(fake_agy, scenario='text', **kwargs):
    command, env, record, set_scenario = fake_agy
    set_scenario(scenario)
    # One agy per request here; pre-warming has its own tests (tests/test_perf_prewarm.py).
    env['ANTIGRAVITY_AGY_PREWARM'] = '0'
    return Client(command=command, env=env, **kwargs), record


def wait_gone(path, seconds=10):
    # Workspaces are released off the request path (see transport.reap).
    for _ in range(int(seconds * 20)):
        if not os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def assert_dead(record):
    pid = int((record / 'pid.txt').read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f'fake agy pid {pid} survived the request')


def frontmatter(agent_md):
    head = agent_md.split('---', 2)[1]
    return {line.split(':', 1)[0].strip(): line.split(':', 1)[1].strip() for line in head.splitlines() if ':' in line}


def test_text_response_and_usage(fake_agy):
    c, record = client(fake_agy)
    response = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    choice = response.choices[0]
    assert choice.message.content == 'Hello world\n'
    assert choice.message.tool_calls is None
    assert choice.finish_reason == 'stop'
    assert response.usage.prompt_tokens == 1200
    assert response.usage.completion_tokens == 5
    assert response.usage.prompt_tokens_details.cached_tokens == 200
    assert response.usage.completion_tokens_details.reasoning_tokens == 3
    argv = json.loads((record / 'argv.json').read_text())
    assert argv[argv.index('--model') + 1] == 'gemini-3.8-flash-low'
    assert argv[argv.index('--agent') + 1] == 'hermes-relay'
    assert '--disable-slash-commands' in argv
    stdin = json.loads((record / 'stdin.jsonl').read_text().splitlines()[0])
    assert stdin == {'event': 'user', 'message': {'role': 'user', 'content': 'hi'}}


def test_streaming_chunks(fake_agy):
    c, _ = client(fake_agy)
    chunks = list(c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, stream=True))
    text = ''.join(ch.choices[0].delta.content or '' for ch in chunks)
    assert text == 'Hello world\n'
    assert [ch.choices[0].delta.content for ch in chunks[:2]] == ['Hello', ' world']
    assert chunks[-1].choices[0].finish_reason == 'stop'
    assert chunks[-1].usage.total_tokens == 1205


def test_relay_agent_definition(fake_agy):
    c, record = client(fake_agy)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    agent_md = (record / 'agent.md').read_text()
    meta = frontmatter(agent_md)
    assert meta['name'] == 'hermes-relay'
    assert meta['excludeDefaultComponents'] == 'true'
    assert meta['inheritMcp'] == 'false'
    assert meta['mainAgent'] == 'true'
    assert 'inert_mcp.py' in agent_md and '- name: hermes' in agent_md
    body = agent_md.split('---', 2)[2]
    assert body.lstrip().startswith('You are Hermes.')
    assert body.index('# Tool protocol') < body.index('### terminal') < body.index('### read_file')
    # Compact, deterministic schema JSON.
    assert '{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}' in body
    assert 'call_mcp_tool' in body and 'ServerName' in body
    # The workspace is private and gone after the request.
    assert wait_gone((record / 'cwd.txt').read_text())


def test_no_tools_means_no_mcp_server(fake_agy):
    c, record = client(fake_agy)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS, tool_choice='none')
    agent_md = (record / 'agent.md').read_text()
    assert 'mcpServers' not in agent_md
    assert 'No tools are available' in agent_md


def test_tool_call_is_captured_and_run_killed(fake_agy):
    c, record = client(fake_agy, 'tool')
    started = time.monotonic()
    response = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert time.monotonic() - started < 10
    choice = response.choices[0]
    assert choice.finish_reason == 'tool_calls'
    (call,) = choice.message.tool_calls
    assert call.type == 'function'
    assert call.function.name == 'terminal'
    assert json.loads(call.function.arguments) == {'command': 'ls -la'}
    assert call.id.startswith('call_')
    assert response.usage.prompt_tokens == 1500
    assert_dead(record)
    assert not (record / 'after_tool').exists()


def test_tool_call_streamed_with_preceding_text(fake_agy):
    c, record = client(fake_agy, 'tool_text')
    chunks = list(c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS, stream=True))
    assert chunks[0].choices[0].delta.content == 'Let me look.'
    last = chunks[-1].choices[0]
    assert last.finish_reason == 'tool_calls'
    (call,) = last.delta.tool_calls
    assert call.index == 0
    assert call.function.name == 'read_file'
    assert json.loads(call.function.arguments) == {'path': 'notes.txt'}
    assert_dead(record)


@pytest.mark.parametrize('scenario', ['native_tool', 'foreign_mcp', 'unknown_hermes_tool'])
def test_non_hermes_tool_kills_run(fake_agy, scenario):
    c, record = client(fake_agy, scenario)
    started = time.monotonic()
    with pytest.raises(AgyNativeToolViolation):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert time.monotonic() - started < 10
    assert_dead(record)


def test_native_tool_violation_while_streaming(fake_agy):
    c, record = client(fake_agy, 'native_tool')
    with pytest.raises(AgyNativeToolViolation):
        list(c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS, stream=True))
    assert_dead(record)


def test_agent_fallback_is_refused(fake_agy):
    c, _ = client(fake_agy, 'fallback')
    with pytest.raises(AgyAgentNotLoaded):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)


def test_quota_exhausted(fake_agy):
    c, _ = client(fake_agy, 'quota')
    with pytest.raises(AgyQuotaExhausted, match='quota'):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)


def test_logged_out(fake_agy):
    c, _ = client(fake_agy, 'logged_out')
    with pytest.raises(AgyLoggedOut, match='sign in'):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)


def test_timeout_kills_hung_agy(fake_agy):
    c, record = client(fake_agy, 'hang', timeout=1)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    assert time.monotonic() - started < 10
    assert_dead(record)


def test_crash_reports_stderr(fake_agy):
    c, _ = client(fake_agy, 'crash')
    with pytest.raises(AgyError, match='boom'):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)


def test_non_json_noise_is_tolerated(fake_agy):
    c, _ = client(fake_agy, 'noise')
    assert c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER).choices[0].message.content == 'Hello world\n'


def test_missing_binary(tmp_path):
    c = Client(command=str(tmp_path / 'nope'), env=dict(os.environ))
    with pytest.raises(AgyMissing, match='not installed'):
        c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)


def test_alias_maps_to_native_slug(fake_agy):
    c, record = client(fake_agy)
    c.chat.completions.create(model='pro', messages=USER)
    argv = json.loads((record / 'argv.json').read_text())
    assert argv[argv.index('--model') + 1] == 'gemini-3.1-pro-high'


def test_async_create(fake_agy):
    c, _ = client(fake_agy)

    async def run():
        return await c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER)

    assert asyncio.run(run()).choices[0].message.content == 'Hello world\n'


def test_trace_dir(fake_agy, tmp_path):
    command, env, _, _ = fake_agy
    env['ANTIGRAVITY_AGY_TRACE_DIR'] = str(tmp_path / 'trace')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    (trace,) = (tmp_path / 'trace').iterdir()
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [r['event'] for r in rows[:2]] == ['_request', '_spawn'] and rows[-1]['event'] == '_done'
    assert rows[0]['model'] == 'gemini-3.8-flash-low' and rows[0]['tools'] == 0
    events = [r for r in rows if not r['event'].startswith('_')]
    assert events[0]['event'] == 'init' and events[-1]['event'] == 'result'
    stamps = [r['_ts'] for r in rows]
    assert stamps == sorted(stamps)


def test_multi_turn_history_is_flattened(fake_agy):
    c, record = client(fake_agy)
    messages = [
        {'role': 'system', 'content': 'You are Hermes.'},
        {'role': 'user', 'content': 'Read notes.txt'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path": "notes.txt"}'}}]},
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'the secret word is ORCHID'},
    ]
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=messages, tools=TOOLS)
    prompt = json.loads((record / 'stdin.jsonl').read_text().splitlines()[0])['message']['content']
    assert '<message role="user">\nRead notes.txt\n</message>' in prompt
    assert '<tool_call id="call_1" name="read_file">{"path": "notes.txt"}</tool_call>' in prompt
    assert '<message role="tool" tool_call_id="call_1" name="read_file">\nthe secret word is ORCHID\n</message>' in prompt
    assert 'You are Hermes.' not in prompt  # the system prompt lives in the agent definition


# ── pure helpers ──────────────────────────────────────────────────────────────────────────────

def test_prepare_single_turn():
    assert prepare_conversation(USER) == ('You are Hermes.', 'hi')


def test_prepare_rejects_assistant_prefill():
    with pytest.raises(ValueError, match='end in a user or tool'):
        prepare_conversation(USER + [{'role': 'assistant', 'content': 'partial'}])


def test_prepare_images_become_placeholders():
    _, prompt = prepare_conversation([{'role': 'user', 'content': [
        {'type': 'text', 'text': 'what is this?'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}}]}])
    assert 'what is this?' in prompt and transport.IMAGE_PLACEHOLDER in prompt


def test_prepare_late_system_message_stays_in_transcript():
    system, prompt = prepare_conversation([
        {'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'},
        {'role': 'system', 'content': 'context was compacted'}, {'role': 'user', 'content': 'c'}])
    assert system == ''
    assert '<message role="system">\ncontext was compacted\n</message>' in prompt


def test_duplicate_tool_names_rejected():
    with pytest.raises(ValueError, match='unique'):
        transport.tool_manifest(TOOLS + TOOLS[:1])


def test_schema_normalized_to_object():
    manifest, names = transport.tool_manifest([{'type': 'function', 'function': {
        'name': 'x', 'parameters': {'anyOf': [{'type': 'object'}]}}}])
    assert names == {'x'}
    assert manifest[0]['inputSchema'] == {'type': 'object', 'properties': {}}


def test_agy_exiting_before_reading_prompt_reports_its_error(fake_agy):
    # Real agy rejects a bad --model and exits before reading stdin; the prompt write then hits a
    # closed pipe. The caller must see agy's own reason, not a bare (retryable) BrokenPipeError.
    c, _ = client(fake_agy, 'bad_model')
    c.env['ANTIGRAVITY_AGY_PREWARM'] = '0'
    for _ in range(3):  # the exit/write race goes either way; every outcome must be the clear error
        with pytest.raises(AgyError, match='requires --effort') as info:
            c.chat.completions.create(model='gemini-9-flash-x', messages=USER, tools=TOOLS)
        assert not isinstance(info.value.__cause__, BrokenPipeError)


def test_send_to_exited_process_does_not_raise(fake_agy):
    command, env, _, set_scenario = fake_agy
    set_scenario('bad_model')
    proc = transport.AgyProcess([command], 'gemini-9-flash-x', 'sys', [], env)
    proc.p.wait(timeout=10)
    proc.send('hello')  # must not raise BrokenPipeError
    proc.kill()
    proc.dispose(0)
