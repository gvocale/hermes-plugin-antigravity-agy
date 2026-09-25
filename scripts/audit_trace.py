#!/usr/bin/env python3
"""Summarize ANTIGRAVITY_AGY_TRACE_DIR recordings: per agy run, the agent that ran, how the run
ended and every tool step. Exits 1 if any tool step is not a Hermes call_mcp_tool dispatch, or a
Hermes dispatch ever reached DONE/ERROR (i.e. agy got an answer, which the inert server never gives).

usage: scripts/audit_trace.py <trace-dir>
"""
import glob
import json
import os
import sys


def main(trace_dir):
    violations = 0
    for path in sorted(glob.glob(os.path.join(trace_dir, '*.ndjson'))):
        agent, status, usage, tools, warm = None, None, None, [], None
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                event = json.loads(line)
                if event.get('event') == '_spawn':
                    warm = event.get('warm')
                if event.get('event') == 'init':
                    agent = event['init'].get('agent')
                step = event.get('step_update') or {}
                if step.get('step_type') == 'tool':
                    params = (step.get('tool_info') or {}).get('parameters') or {}
                    tools.append((step.get('state'), step.get('tool_name'), params.get('ServerName'), params.get('ToolName'),
                                  json.dumps(params.get('Arguments'), ensure_ascii=False)))
                elif step.get('step_type') == 'agent_response' and step.get('state') == 'DONE':
                    usage = step.get('usage')
                if event.get('event') == 'result':
                    status, usage = event['result'].get('status'), event['result'].get('usage')
        print(f'{os.path.basename(path)} agent={agent} end={status or "killed-at-tool-call"} '
              + ('' if warm is None else f'prewarmed={bool(warm)} ')
              + f'input_tokens={(usage or {}).get("input_tokens")} cache_read={(usage or {}).get("cache_read_tokens")}')
        for state, name, server, tool, args in tools:
            ok = name == 'call_mcp_tool' and server == 'hermes' and state == 'ACTIVE'
            violations += not ok
            print(f'    {"ok  " if ok else "VIOLATION"} {state} {name} -> {server}/{tool} {args[:100]}')
    print(f'violations: {violations}')
    return 1 if violations else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
