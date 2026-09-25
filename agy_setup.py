"""Setup-time probes of the user's agy CLI: login/quota state and its own model list.

Both probes spend no model quota: ``agy -p=/quota`` is answered locally by print mode without an
agent turn, and ``agy models`` only lists the account's picker. Neither touches agy's credential
files; agy itself owns authentication. Anything unexpected returns ``None``/``False`` so callers
fall back to the static catalog rather than failing setup.
"""
import json
import os
import shutil
import subprocess
import tempfile

COMMAND_ENV = 'ANTIGRAVITY_AGY_COMMAND'
INSTALL_HINT = ('The Antigravity CLI is not installed (no `agy` on PATH). Install it from '
                'https://antigravity.google/docs/cli/install or set ANTIGRAVITY_AGY_COMMAND to the binary.')
LOGIN_HINT = 'agy is installed but not signed in. Run `agy` once interactively to sign in, then retry.'


def _resolve(command, env):
    """The agy argv head as an absolute path (plus any extra argv), or ``None`` when missing."""
    command = list(command) if command else [env.get(COMMAND_ENV) or 'agy']
    head = command[0]
    exe = head if os.path.isabs(head) and os.access(head, os.X_OK) else shutil.which(head, path=env.get('PATH') or os.defpath)
    return ([exe] + command[1:]) if exe else None


def _run(argv, env, timeout):
    # A neutral cwd keeps workspace customizations out of the probe.
    with tempfile.TemporaryDirectory(prefix='agy-hermes-probe-') as cwd:
        return subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)


def quota(command=None, env=None, timeout=30):
    """``(ok, rows, detail)`` from ``agy -p=/quota``; rows are ``(group, bucket, remaining, reset)``."""
    env = dict(env if env is not None else os.environ)
    resolved = _resolve(command, env)
    if resolved is None:
        return False, [], INSTALL_HINT
    try:
        run = _run(resolved + ['-p=/quota', '--output-format', 'json'], env, timeout)
        payload = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        return False, [], f'agy quota probe failed: {error}'
    if payload.get('status') != 'SUCCESS':
        detail = str(payload.get('error') or run.stderr.strip() or 'agy quota probe failed')
        return False, [], detail[:500]
    rows = [tuple(line.split('\t')) for line in str(payload.get('response') or '').splitlines() if line.count('\t') >= 2]
    return True, rows, ''


def setup_status(command=None, env=None, timeout=30):
    """``{available, logged_in, plan, detail, login_command}`` from agy itself."""
    env = dict(env if env is not None else os.environ)
    resolved = _resolve(command, env)
    if resolved is None:
        return {'available': False, 'logged_in': False, 'plan': '', 'detail': INSTALL_HINT, 'login_command': None}
    ok, rows, detail = quota(resolved, env, timeout)
    summary = '; '.join(f'{row[0]} {row[1]}: {row[2]}' for row in rows if len(row) >= 3)
    return {'available': True, 'logged_in': ok, 'plan': 'Google Antigravity subscription' if ok else '',
            'detail': summary if ok else f'{LOGIN_HINT} ({detail})', 'login_command': resolved}


def discover_models(command=None, env=None, timeout=40):
    """The account's live picker as ``[{id, label, note}]``, or ``None`` when agy cannot answer."""
    env = dict(env if env is not None else os.environ)
    resolved = _resolve(command, env)
    if resolved is None:
        return None
    try:
        run = _run(resolved + ['models'], env, timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0:
        return None
    rows = []
    for line in run.stdout.splitlines():
        slug, sep, label = line.partition('\t')
        slug = slug.strip()
        if sep and slug and ' ' not in slug:
            rows.append({'id': slug, 'label': label.strip() or slug, 'note': ''})
    return rows or None
