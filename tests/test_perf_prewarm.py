"""Pre-warmed agy processes, background shutdown and conversation cleanup (fake agy only)."""
import json
import os
from pathlib import Path
import stat
import sys
import time

import pytest

import transport
from transport import Client

TOOLS = [
    {'type': 'function', 'function': {'name': 'terminal', 'description': 'Run a shell command.', 'parameters': {
        'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}},
]
USER = [{'role': 'system', 'content': 'You are Hermes.'}, {'role': 'user', 'content': 'hi'}]


@pytest.fixture(autouse=True)
def fresh_pool(monkeypatch):
    pool = transport.WarmPool()
    monkeypatch.setattr(transport, 'POOL', pool)
    yield pool
    pool.drain()


@pytest.fixture
def warm(fake_agy, tmp_path):
    command, env, record, set_scenario = fake_agy
    env['ANTIGRAVITY_AGY_TRACE_DIR'] = str(tmp_path / 'trace')
    env['FAKE_AGY_RECORD_ALL'] = '1'
    env['ANTIGRAVITY_AGY_PREWARM'] = '1'
    return command, env, record, set_scenario


def traces(tmp_path):
    rows = []
    for path in sorted((tmp_path / 'trace').glob('*.ndjson'), key=lambda p: p.stat().st_mtime_ns):
        rows.append([json.loads(line) for line in path.read_text().splitlines()])
    return rows


def spawn_row(rows):
    return next(r for r in rows if r['event'] == '_spawn')


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(predicate, seconds=10):
    for _ in range(int(seconds * 20)):
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_second_request_of_a_tool_loop_uses_the_prewarmed_agy(warm, tmp_path, fresh_pool):
    command, env, record, set_scenario = warm
    set_scenario('text')
    c = Client(command=command, env=env)
    first = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert first.choices[0].message.content == 'Hello world\n'
    (proc,) = fresh_pool.procs()  # booted for the next request, while the first one ran
    assert proc.alive()
    assert wait_until(lambda: not proc.events.empty())  # booted: init already emitted
    second = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert second.choices[0].message.content == 'Hello world\n'
    first_trace, second_trace = traces(tmp_path)
    assert spawn_row(first_trace)['warm'] is False
    assert spawn_row(second_trace)['warm'] is True and spawn_row(second_trace)['warm_age'] > 0
    # The pre-warmed process emitted init before the request existed: negative _ts.
    init = next(r for r in second_trace if r['event'] == 'init')
    assert init['_ts'] < 0
    # Every agy saw exactly one prompt, and only after its request adopted it.
    prompts = [p.read_text() for p in record.glob('stdin-*.jsonl')]
    assert len(prompts) == 2 and all(len(p.splitlines()) == 1 for p in prompts)


def test_prewarmed_agy_receives_nothing_until_adopted(warm, fresh_pool):
    command, env, record, set_scenario = warm
    set_scenario('tool')
    c = Client(command=command, env=env)
    response = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert response.choices[0].message.tool_calls[0].function.name == 'terminal'
    (proc,) = fresh_pool.procs()
    time.sleep(0.3)
    # Booted (init queued) but blocked on stdin: no prompt, so no model turn and no tool result.
    assert proc.alive()
    assert not (record / f'stdin-{proc.p.pid}.jsonl').exists()
    assert proc.events.get(timeout=5)['event'] == 'init'


def test_tool_call_on_prewarmed_agy_is_captured_and_killed(warm, tmp_path, fresh_pool):
    command, env, record, set_scenario = warm
    set_scenario('tool')
    c = Client(command=command, env=env)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (proc,) = fresh_pool.procs()
    started = time.monotonic()
    response = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert time.monotonic() - started < 10
    assert response.choices[0].finish_reason == 'tool_calls'
    assert spawn_row(traces(tmp_path)[1])['warm'] is True
    assert wait_until(lambda: not alive(proc.p.pid))
    assert not (record / 'after_tool').exists()


def test_different_system_prompt_starts_cold(warm, tmp_path, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    c = Client(command=command, env=env)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    other = [{'role': 'system', 'content': 'You are someone else.'}, {'role': 'user', 'content': 'hi'}]
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=other, tools=TOOLS)
    assert spawn_row(traces(tmp_path)[1])['warm'] is False
    assert len(fresh_pool) == 2 and len(fresh_pool.entries) == 2  # one per request shape


def test_no_prewarm_without_tools_or_when_disabled(warm, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    assert not fresh_pool.entries  # e.g. Hermes' title generation: no follow-up request to prepare for
    env['ANTIGRAVITY_AGY_PREWARM'] = '0'
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert not fresh_pool.entries


def test_pool_is_bounded(warm, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    c = Client(command=command, env=env)
    for name in ('A', 'B', 'C', 'D', 'E'):
        c.chat.completions.create(model='gemini-3.8-flash-low', tools=TOOLS,
                                  messages=[{'role': 'system', 'content': name}, {'role': 'user', 'content': 'hi'}])
    assert len(fresh_pool) == transport.POOL_MAX


def test_unused_prewarmed_agy_expires(warm, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    env['ANTIGRAVITY_AGY_PREWARM_TTL'] = '0.5'
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (proc,) = fresh_pool.procs()
    workspace = proc.root
    assert wait_until(lambda: not fresh_pool.entries and not alive(proc.p.pid) and not workspace.exists())


def test_drain_kills_prewarmed_agy(warm, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (proc,) = fresh_pool.procs()
    fresh_pool.drain()
    assert not fresh_pool.entries
    assert wait_until(lambda: not alive(proc.p.pid))
    assert not proc.root.exists()


def test_dead_prewarmed_agy_is_not_used(warm, tmp_path, fresh_pool):
    command, env, _, set_scenario = warm
    set_scenario('text')
    c = Client(command=command, env=env)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (proc,) = fresh_pool.procs()
    proc.kill()
    assert wait_until(lambda: not proc.alive())
    response = c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert response.choices[0].message.content == 'Hello world\n'
    assert spawn_row(traces(tmp_path)[1])['warm'] is False


def test_answer_returns_before_agy_session_shutdown(fake_agy):
    command, env, record, set_scenario = fake_agy
    set_scenario('slow_exit')
    env['ANTIGRAVITY_AGY_PREWARM'] = '0'
    started = time.monotonic()
    response = Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    assert response.choices[0].message.content == 'Bye'
    assert time.monotonic() - started < 2.5  # fake agy needs 3 s to shut down after its result
    workspace = (record / 'cwd.txt').read_text()
    # Shutdown finishes in the background (not killed mid-way), then the workspace is released.
    assert wait_until(lambda: (record / 'slow_exit_done').exists() and not os.path.exists(workspace))


def make_trash(tmp_path):
    bin_dir, bin_trash = tmp_path / 'bin', tmp_path / 'trash'
    bin_dir.mkdir()
    bin_trash.mkdir()
    script = bin_dir / 'trash-put'
    script.write_text(f'#!{sys.executable}\nimport shutil, sys\nfor p in sys.argv[1:]:\n'
                      f'    if p != "--": shutil.move(p, {str(bin_trash)!r})\n', encoding='utf-8')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir, bin_trash


@pytest.mark.parametrize('scenario', ['text', 'tool'])
def test_cleanup_trashes_only_this_runs_conversation(fake_agy, tmp_path, scenario):
    command, env, _, set_scenario = fake_agy
    set_scenario(scenario)
    data = tmp_path / 'agy-data'
    other = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    (data / 'conversations').mkdir(parents=True)
    (data / 'conversations' / f'{other}.db').write_text('not ours')
    (data / 'conversation_summaries.db').write_text('shared store')
    bin_dir, bin_trash = make_trash(tmp_path)
    env.update(FAKE_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_CLEANUP='trash',
               ANTIGRAVITY_AGY_PREWARM='0', PATH=f'{bin_dir}{os.pathsep}{env["PATH"]}')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    assert wait_until(lambda: len(list(bin_trash.iterdir())) == 3)
    trashed = sorted(p.name for p in bin_trash.iterdir())
    conv = next(n for n in trashed if n.endswith('.db'))[:-3]
    assert trashed == sorted([f'{conv}.db', conv, f'{conv}.lock'])
    assert (data / 'conversations' / f'{other}.db').read_text() == 'not ours'
    assert (data / 'conversation_summaries.db').exists()
    assert not list((data / 'brain').iterdir())


def test_used_records_are_kept_by_default_unused_prewarmed_ones_trashed(fake_agy, tmp_path, fresh_pool):
    command, env, _, set_scenario = fake_agy
    set_scenario('text')
    data = tmp_path / 'agy-data'
    bin_dir, bin_trash = make_trash(tmp_path)
    env.update(FAKE_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_PREWARM='1',
               PATH=f'{bin_dir}{os.pathsep}{env["PATH"]}')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (warm_proc,) = fresh_pool.procs()
    assert wait_until(lambda: warm_proc.conversation_id is not None)
    assert len(list((data / 'conversations').iterdir())) == 2
    fresh_pool.drain()  # what interpreter exit does
    kept = [p.name for p in (data / 'conversations').iterdir()]
    assert len(kept) == 1 and warm_proc.conversation_id not in kept[0]
    assert sorted(p.name for p in bin_trash.iterdir()) == sorted(
        [f'{warm_proc.conversation_id}.db', warm_proc.conversation_id, f'{warm_proc.conversation_id}.lock'])


def test_cleanup_off(fake_agy, tmp_path, fresh_pool):
    command, env, _, set_scenario = fake_agy
    set_scenario('text')
    data = tmp_path / 'agy-data'
    bin_dir, bin_trash = make_trash(tmp_path)
    env.update(FAKE_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_PREWARM='1',
               ANTIGRAVITY_AGY_CLEANUP='off', PATH=f'{bin_dir}{os.pathsep}{env["PATH"]}')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    (warm_proc,) = fresh_pool.procs()
    assert wait_until(lambda: warm_proc.conversation_id is not None)
    fresh_pool.drain()
    time.sleep(0.3)
    assert len(list((data / 'conversations').iterdir())) == 2
    assert not list(bin_trash.iterdir())


def test_no_cleanup_by_default(fake_agy, tmp_path):
    command, env, _, set_scenario = fake_agy
    set_scenario('text')
    data = tmp_path / 'agy-data'
    bin_dir, bin_trash = make_trash(tmp_path)
    env.update(FAKE_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_DATA_DIR=str(data), ANTIGRAVITY_AGY_PREWARM='0',
               PATH=f'{bin_dir}{os.pathsep}{env["PATH"]}')
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    time.sleep(0.5)  # no pre-warming without tools; the used record stays
    assert len(list((data / 'conversations').iterdir())) == 1
    assert not list(bin_trash.iterdir())


def test_conversation_paths_reject_non_uuid(tmp_path):
    (tmp_path / 'conversations').mkdir()
    (tmp_path / 'conversations' / '..db').write_text('x')
    assert transport.conversation_paths('.', tmp_path) == []
    assert transport.conversation_paths('../conversations', tmp_path) == []
    assert transport.conversation_paths('', tmp_path) == []


def test_default_depth_keeps_two_ready(fake_agy, tmp_path, fresh_pool):
    command, env, _, set_scenario = fake_agy
    set_scenario('text')
    env['ANTIGRAVITY_AGY_TRACE_DIR'] = str(tmp_path / 'trace')
    env.pop('ANTIGRAVITY_AGY_PREWARM', None)
    c = Client(command=command, env=env)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    first = fresh_pool.procs()
    assert len(first) == transport.DEFAULT_PREWARM_DEPTH == 2
    assert wait_until(lambda: all(not p.events.empty() for p in first))
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    c.chat.completions.create(model='gemini-3.8-flash-low', messages=USER, tools=TOOLS)
    # Turns 2 and 3 both ran on processes booted during turn 1 (oldest first), and the pool was topped up.
    spawns = [spawn_row(rows) for rows in traces(tmp_path)]
    assert [s['warm'] for s in spawns] == [False, True, True]
    assert len(fresh_pool) == 2 and not set(first) & set(fresh_pool.procs())


@pytest.mark.parametrize('raw,depth', [('0', 0), ('off', 0), ('1', 1), ('2', 2), ('9', 4), ('yes', 2), ('junk', 2)])
def test_prewarm_depth_parsing(raw, depth):
    assert transport.prewarm_depth({'ANTIGRAVITY_AGY_PREWARM': raw}) == depth
    assert transport.prewarm_depth({}) == 2


@pytest.mark.parametrize('raw,mode', [(None, 'unused'), ('trash', 'all'), ('all', 'all'), ('off', 'off'), ('0', 'off'),
                                      ('unused', 'unused'), ('junk', 'unused')])
def test_cleanup_mode_parsing(raw, mode):
    assert transport.cleanup_mode({} if raw is None else {'ANTIGRAVITY_AGY_CLEANUP': raw}) == mode


def test_exit_finishes_background_shutdowns(fake_agy, monkeypatch):
    command, env, record, set_scenario = fake_agy
    set_scenario('slow_exit')
    env['ANTIGRAVITY_AGY_PREWARM'] = '0'
    Client(command=command, env=env).chat.completions.create(model='gemini-3.8-flash-low', messages=USER)
    workspace = (record / 'cwd.txt').read_text()
    assert os.path.exists(workspace) and transport._REAPING
    started = time.monotonic()
    transport._shutdown()
    assert time.monotonic() - started < 2.5  # cut short, not the full 3 s agy shutdown
    assert not os.path.exists(workspace)
    pid = int((record / 'pid.txt').read_text())
    assert wait_until(lambda: not alive(pid))
