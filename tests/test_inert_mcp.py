import json
from pathlib import Path
import subprocess
import sys

from conftest import PLUGIN_DIR

MANIFEST = [{'name': 'terminal', 'description': 'Run a command.',
             'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}]


def test_inventory_answers_and_calls_are_captured_not_executed(tmp_path):
    manifest = tmp_path / 'tools.json'
    manifest.write_text(json.dumps(MANIFEST))
    capture = tmp_path / 'capture.jsonl'
    sentinel = tmp_path / 'should-not-exist'
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list', 'params': {}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'terminal', 'arguments': {'command': f'touch {sentinel}'}}},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'resources/list', 'params': {}},
    ]
    run = subprocess.run([sys.executable, str(PLUGIN_DIR / 'inert_mcp.py'), str(manifest), str(capture)],
                         input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True, timeout=30)
    replies = {r['id']: r for r in map(json.loads, run.stdout.splitlines())}
    assert set(replies) == {1, 2, 3, 5}  # tools/call (id 4) is never answered
    assert replies[2]['result']['protocolVersion'] == '2025-11-25'
    assert replies[3]['result']['tools'] == MANIFEST
    assert replies[5]['error']['code'] == -32601
    assert json.loads(capture.read_text()) == {'name': 'terminal', 'arguments': {'command': f'touch {sentinel}'}}
    assert not sentinel.exists()


def test_inert_server_imports_nothing_from_host():
    source = Path(PLUGIN_DIR / 'inert_mcp.py').read_text()
    imports = {line.split()[1] for line in source.splitlines() if line.startswith(('import ', 'from '))}
    assert imports <= {'json', 'pathlib', 'sys'}
