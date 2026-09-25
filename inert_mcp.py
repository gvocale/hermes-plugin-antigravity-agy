"""Inventory-and-capture MCP server. No host imports, effects or executable tool implementations.

agy launches this over stdio. ``tools/list`` advertises Hermes' tool manifest; ``tools/call`` is
recorded to the capture file and never answered, so agy blocks on it until the transport kills
the run and hands the call to Hermes. Nothing is ever executed here.

usage: inert_mcp.py <manifest.json> <capture.jsonl>
"""
import json
from pathlib import Path
import sys


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    capture = open(sys.argv[2], 'a', encoding='utf-8')
    for line in sys.stdin:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        method = row.get('method')
        if method == 'tools/call':
            params = row.get('params') or {}
            capture.write(json.dumps({'name': params.get('name'), 'arguments': params.get('arguments') or {}}) + '\n')
            capture.flush()
            continue  # deliberately unanswered: Hermes, not agy, executes tools
        if 'id' not in row:
            continue
        if method == 'initialize':
            version = (row.get('params') or {}).get('protocolVersion') or '2024-11-05'
            reply = {'result': {'protocolVersion': version, 'capabilities': {'tools': {}},
                                'serverInfo': {'name': 'hermes', 'version': '1'}}}
        elif method == 'tools/list':
            reply = {'result': {'tools': manifest}}
        elif method in ('ping', 'server/discover'):
            reply = {'result': {}}
        else:
            reply = {'error': {'code': -32601, 'message': f'Method not found: {method}'}}
        print(json.dumps({'jsonrpc': '2.0', 'id': row['id'], **reply}), flush=True)


if __name__ == '__main__':
    main()
