#!/usr/bin/env python3
"""Real end-to-end benchmark for the antigravity-agy provider (spends a little subscription quota).

Each run executes ``hermes chat -q ... --provider antigravity-agy`` in a fresh isolated
``HERMES_HOME`` whose ``plugins/antigravity-agy`` symlink points at ``--plugin-dir``, with
``ANTIGRAVITY_AGY_TRACE_DIR`` set, then derives per-agy-run timings from the timestamped trace
(``_request``/``_spawn``/``_ts``/``_done`` lines written by transport.py) and audits it.

Tasks:
  qa    single-turn question ("capital of France", expect "Paris")
  tool  two-step tool task: read_file notes.txt, then terminal `wc -l notes.txt`

usage:
  scripts/bench.py --plugin-dir . --runs 3 --out bench-baseline.json
  scripts/bench.py --compare bench-baseline.json bench-after.json

Metrics per agy run (all seconds are relative to the moment Hermes issued the request):
  startup  wait until agy's ``init`` event (0 when a pre-warmed process was already initialized)
  ttft     first model output (text delta or tool step)
  latency  request -> final chunk handed to Hermes (``_done``)
  tokens   input_tokens / cache_read_tokens / output_tokens as agy reported them
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'scripts'))
QA_PROMPT = 'What is the capital of France? Answer in one word.'
TOOL_PROMPT = ("Step 1: use the read_file tool to read notes.txt in the current directory and find the secret word. "
               "Step 2: use the terminal tool to run 'wc -l notes.txt'. Then reply with the secret word and the line "
               "count reported by wc.")
NOTES = 'shopping list for the week\nthe secret word is ORCHID-42\nbuy milk\ncall the plumber\nwater the plants\n'


def git_rev(path):
    try:
        out = subprocess.run(['git', '-C', str(path), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--', '.'], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() + ('-dirty' if dirty.stdout.strip() else '')
    except Exception:
        return None


def version(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, cwd='/tmp').stdout.strip().splitlines()[0]
    except Exception:
        return None


def make_home(work, plugin_dir):
    home = work / 'home'
    (home / 'plugins').mkdir(parents=True)
    (home / 'config.yaml').write_text('plugins:\n  enabled: []\n  disabled: []\n', encoding='utf-8')
    (home / 'plugins' / 'antigravity-agy').symlink_to(Path(plugin_dir).resolve())
    return home


def parse_trace(path):
    """One agy run -> metrics dict (None fields when the trace predates timestamps)."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    run = {'file': Path(path).name, 'tools': None, 'warm': False, 'startup': None, 'ttft': None, 'latency': None,
           'end': 'killed-at-tool-call', 'input_tokens': 0, 'cache_read_tokens': 0, 'output_tokens': 0,
           'conversation_id': None, 'tool_steps': []}
    usage_steps, final_usage = [], None
    for row in rows:
        kind, ts = row.get('event'), row.get('_ts')
        if kind == '_request':
            run['tools'] = row.get('tools')
            run['wall_start'] = row.get('wall')
        elif kind == '_spawn':
            run['warm'] = bool(row.get('warm'))
        elif kind == '_done':
            run['latency'] = ts
        elif kind == 'init':
            run['startup'] = max(ts, 0.0) if ts is not None else None
            run['conversation_id'] = row.get('conversation_id')
        elif kind == 'step_update':
            step = row.get('step_update') or {}
            if step.get('step_type') == 'agent_response':
                if run['ttft'] is None and step.get('text_delta'):
                    run['ttft'] = ts
                if step.get('state') == 'DONE' and isinstance(step.get('usage'), dict):
                    usage_steps.append(step['usage'])
            elif step.get('step_type') == 'tool':
                if run['ttft'] is None:
                    run['ttft'] = ts
                params = (step.get('tool_info') or {}).get('parameters') or {}
                run['tool_steps'].append(params.get('ToolName'))
        elif kind == 'result':
            result = row.get('result') or {}
            run['end'] = result.get('status')
            final_usage = result.get('usage')
    usage = final_usage if isinstance(final_usage, dict) else {}
    if not usage:
        for step in usage_steps:
            for key, value in step.items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
    for key in ('input_tokens', 'cache_read_tokens', 'output_tokens'):
        run[key] = int(usage.get(key, 0))
    return run


def lingering(marker):
    """PIDs still carrying this run's environment marker (agy processes that outlived Hermes)."""
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if marker.encode() in (proc / 'environ').read_bytes().split(b'\0'):
                found.append(int(proc.name))
        except OSError:
            continue
    return found


def run_task(task, index, plugin_dir, model, root):
    work = root / f'{task}-{index}'
    work.mkdir(parents=True)
    home = make_home(work, plugin_dir)
    cwd = work / 'task'
    cwd.mkdir()
    trace = work / 'trace'
    env = dict(os.environ, HERMES_HOME=str(home), ANTIGRAVITY_AGY_TRACE_DIR=str(trace))
    command = ['hermes', 'chat', '-q', QA_PROMPT if task == 'qa' else TOOL_PROMPT,
               '--provider', 'antigravity-agy', '--model', model, '-Q']
    if task == 'tool':
        (cwd / 'notes.txt').write_text(NOTES, encoding='utf-8')
        command.append('--yolo')
    started = time.monotonic()
    proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    wall = time.monotonic() - started
    time.sleep(3)
    leftover = lingering(f'ANTIGRAVITY_AGY_TRACE_DIR={trace}')
    output = proc.stdout.strip()
    ok = ('paris' in output.lower()) if task == 'qa' else ('ORCHID-42' in output and '5' in output)
    runs = [parse_trace(p) for p in sorted(trace.glob('*.ndjson'))] if trace.exists() else []
    audit = subprocess.run([sys.executable, str(REPO / 'scripts' / 'audit_trace.py'), str(trace)],
                           capture_output=True, text=True) if trace.exists() else None
    violations = None
    if audit is not None:
        for line in audit.stdout.splitlines():
            if line.startswith('violations:'):
                violations = int(line.split(':')[1])
    main = main_runs({'runs': runs})
    record = {'task': task, 'index': index, 'wall': round(wall, 2), 'exit_code': proc.returncode, 'ok': ok,
              'output_tail': output[-300:], 'stderr_tail': proc.stderr.strip()[-300:], 'violations': violations,
              'agy_runs': len(runs), 'main_turns': len(main), 'lingering_pids_after_exit': leftover,
              'main_latency_sum': round(sum(r['latency'] or 0 for r in main), 2) if main else None,
              'runs': runs, 'work_dir': str(work)}
    print(f'  {task}#{index}: wall={wall:.1f}s ok={ok} exit={proc.returncode} agy_runs={len(runs)} '
          f'main_turns={len(main)} violations={violations} lingering={len(leftover)}', flush=True)
    for r in runs:
        print(f'      {"main" if r in main else "aux "} warm={int(r["warm"])} startup={r["startup"]} ttft={r["ttft"]} '
              f'latency={r["latency"]} in={r["input_tokens"]} cache={r["cache_read_tokens"]} end={r["end"]}', flush=True)
    return record


def mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 2) if values else None


def summarize(records):
    summary = {}
    for task in sorted({r['task'] for r in records}):
        rows = [r for r in records if r['task'] == task]
        main = [run for r in rows for run in r['runs'] if run in main_runs(r)]
        summary[task] = {
            'n': len(rows), 'ok': sum(r['ok'] for r in rows), 'violations': sum(r['violations'] or 0 for r in rows),
            'wall_mean': mean([r['wall'] for r in rows]),
            'wall_median': round(statistics.median([r['wall'] for r in rows]), 2),
            'main_turns_mean': mean([r['main_turns'] for r in rows]),
            'turn_latency_mean': mean([m['latency'] for m in main]),
            'startup_mean': mean([m['startup'] for m in main]),
            'ttft_mean': mean([m['ttft'] for m in main]),
            'input_tokens_mean': mean([m['input_tokens'] for m in main]),
            'cache_read_mean': mean([m['cache_read_tokens'] for m in main]),
            'cache_hit_turns': sum(1 for m in main if m['cache_read_tokens']),
            'warm_turns': sum(1 for m in main if m['warm']),
            'main_turns_total': len(main),
            'lingering_after_exit': sum(len(r.get('lingering_pids_after_exit') or []) for r in rows),
        }
    return summary


def main_runs(record):
    main = [r for r in record['runs'] if r['tools']]
    return main or [r for r in record['runs'] if r['tool_steps'] or r['input_tokens'] > 5000]


def bench(args):
    plugin_dir = Path(args.plugin_dir).resolve()
    if not (plugin_dir / 'plugin.yaml').exists():
        sys.exit(f'{plugin_dir} is not a plugin directory (no plugin.yaml)')
    root = Path(args.work_dir or tempfile.mkdtemp(prefix='agy-bench-')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    print(f'work dir: {root}', flush=True)
    records = []
    for index in range(args.runs):
        for task in args.tasks.split(','):
            records.append(run_task(task, index, plugin_dir, args.model, root))
    data = {'label': args.label or plugin_dir.name, 'date': datetime.datetime.now().isoformat(timespec='seconds'),
            'plugin_dir': str(plugin_dir), 'plugin_rev': git_rev(plugin_dir), 'model': args.model,
            'agy_version': version(['agy', '--version']), 'hermes_version': version(['hermes', '--version']),
            'summary': summarize(records), 'records': records}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'wrote {args.out}')
    print(json.dumps(data['summary'], indent=1))
    return 0 if all(r['ok'] and not r['violations'] for r in records) else 1


METRICS = [('wall_mean', 'task wall time, mean (s)'), ('wall_median', 'task wall time, median (s)'),
           ('main_turns_mean', 'agy turns per task'), ('turn_latency_mean', 'per-turn latency, mean (s)'),
           ('startup_mean', 'agy startup wait, mean (s)'), ('ttft_mean', 'first output, mean (s)'),
           ('input_tokens_mean', 'input tokens per turn'), ('cache_read_mean', 'cache_read tokens per turn'),
           ('cache_hit_turns', 'turns with cache hit'), ('warm_turns', 'turns on a pre-warmed agy'),
           ('main_turns_total', 'turns measured'), ('lingering_after_exit', 'agy processes left after Hermes exit'), ('ok', 'correct answers'), ('violations', 'audit violations')]


def compare(a_path, b_path):
    a, b = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (a_path, b_path))
    lines = [f'A = `{a_path}` ({a.get("label")}, rev {a.get("plugin_rev")}, {a.get("date")})  ',
             f'B = `{b_path}` ({b.get("label")}, rev {b.get("plugin_rev")}, {b.get("date")})', '']
    for task in sorted(set(a['summary']) | set(b['summary'])):
        sa, sb = a['summary'].get(task, {}), b['summary'].get(task, {})
        lines += [f'### {task} (A n={sa.get("n")}, B n={sb.get("n")})', '', '| metric | A | B | change |', '| --- | --- | --- | --- |']
        for key, label in METRICS:
            va, vb = sa.get(key), sb.get(key)
            change = ''
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va:
                change = f'{(vb - va) / va * 100:+.0f}%'
            lines.append(f'| {label} | {va} | {vb} | {change} |')
        lines.append('')
    print('\n'.join(lines))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plugin-dir', help='plugin directory the isolated home symlinks to')
    parser.add_argument('--runs', type=int, default=3, help='repetitions per task (default 3)')
    parser.add_argument('--out', help='write raw JSON here')
    parser.add_argument('--tasks', default='qa,tool', help='comma list of qa,tool (default both)')
    parser.add_argument('--model', default='gemini-3.8-flash-low')
    parser.add_argument('--label', help='label stored in the JSON')
    parser.add_argument('--work-dir', help='where homes/traces go (default: a new /tmp/agy-bench-* dir)')
    parser.add_argument('--compare', nargs=2, metavar=('A_JSON', 'B_JSON'), help='print a markdown comparison and exit')
    args = parser.parse_args()
    if args.compare:
        return compare(*args.compare)
    if not args.plugin_dir:
        parser.error('--plugin-dir is required unless --compare is given')
    return bench(args)


if __name__ == '__main__':
    sys.exit(main())
