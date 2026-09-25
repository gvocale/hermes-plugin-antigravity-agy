"""Test bootstrap and shared fixtures.

The plugin imports Hermes core (``providers``, ``tools``) from a checkout: set
``HERMES_AGENT_REPO`` to your hermes-agent clone (default ``~/.hermes/hermes-agent``).
Every test drives ``tests/fake_agy.py``; nothing here spends Antigravity quota.
"""
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

HERMES_AGENT_REPO = Path(os.environ.get('HERMES_AGENT_REPO') or Path.home() / '.hermes' / 'hermes-agent').expanduser()
REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT  # the plugin lives at the repository root, as `hermes plugins install` expects
PLUGIN_NAME = 'antigravity-agy'
FAKE_AGY = Path(__file__).with_name('fake_agy.py')

for path in (str(HERMES_AGENT_REPO), str(PLUGIN_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def fake_agy(tmp_path):
    """``(command, env, record_dir, set_scenario)`` wired to the fake CLI."""
    FAKE_AGY.chmod(FAKE_AGY.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / 'record'
    env = dict(os.environ)
    env.pop('ANTIGRAVITY_AGY_TRACE_DIR', None)
    env.update(FAKE_AGY_SCENARIO='text', FAKE_AGY_RECORD=str(record))

    def scenario(name):
        env['FAKE_AGY_SCENARIO'] = name

    return str(FAKE_AGY), env, record, scenario


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Load the plugin exactly as an install leaves it — ``$HERMES_HOME/plugins/<name>/`` with
    ``kind: model-provider`` — through Hermes core's provider discovery."""
    import providers

    home = tmp_path / 'hermes-home'
    installed = home / 'plugins' / PLUGIN_NAME
    shutil.copytree(PLUGIN_DIR, installed, ignore=shutil.ignore_patterns(
        '.git', '.github', '.venv', 'tests', 'scripts', 'docs', '__pycache__', '.hermes-test*'))
    monkeypatch.setenv('HERMES_HOME', str(home))
    (home / 'config.yaml').write_text('plugins:\n  enabled: []\n', encoding='utf-8')
    # A machine with agy installed must not leak it into offline tests.
    monkeypatch.setenv('ANTIGRAVITY_AGY_COMMAND', str(tmp_path / 'no-such-agy'))
    for name in tuple(sys.modules):
        if name.startswith('_hermes_user_provider_'):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(providers, '_REGISTRY', {})
    monkeypatch.setattr(providers, '_ALIASES', {})
    monkeypatch.setattr(providers, '_PROVIDER_LIST_CACHE', None)
    monkeypatch.setattr(providers, '_discovered', False)
    providers._discover_providers()
    result = providers.get_provider_profile(PLUGIN_NAME)
    assert result is not None, 'installed-plugin discovery did not register the provider'
    return result
