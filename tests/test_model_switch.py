"""Interactive ``/model`` switching resolves the plugin through Hermes core, offline.

These drive the same ``switch_model`` pipeline the CLI, TUI and gateway ``/model`` handlers call,
against the plugin installed in a temp ``$HERMES_HOME`` (``profile`` fixture) with the fake agy CLI
as the binary and every HTTP request refused, so nothing leaves the machine.

``hermes_cli`` needs a few more third-party modules than the provider layer the other tests use;
without them this module is skipped. To run it: ``uv pip install requests rich httpx`` into the
test venv.
"""
import pytest

requests = pytest.importorskip('requests')
for _module in ('rich', 'httpx'):
    pytest.importorskip(_module)

PROVIDER = 'antigravity-agy'


@pytest.fixture
def switch_env(profile, fake_agy, monkeypatch):
    """Installed plugin + fake agy on ``ANTIGRAVITY_AGY_COMMAND`` + no network."""
    command, env, _, _ = fake_agy
    monkeypatch.setenv('ANTIGRAVITY_AGY_COMMAND', command)
    for key in ('FAKE_AGY_SCENARIO', 'FAKE_AGY_RECORD'):
        monkeypatch.setenv(key, env[key])

    def refuse(*_, **__):
        raise requests.ConnectionError('offline test: network disabled')

    monkeypatch.setattr(requests.Session, 'request', refuse)
    return profile


def _switch(raw, current_provider='openai-codex', current_model='gpt-5'):
    from hermes_cli.model_switch import parse_model_switch_args, switch_model
    request = parse_model_switch_args(raw)
    return switch_model(request.target, current_provider, current_model, explicit_provider=request.explicit_provider)


def test_provider_resolves_without_config_entry(switch_env):
    # The Phase-1 worry ("resolve_provider_full ignores the plugin registry") does not hold on
    # Hermes >= 0.21.4: the plugin profile resolves with no ``providers:`` block at all.
    from hermes_cli.providers import resolve_provider_full
    pdef = resolve_provider_full(PROVIDER)
    assert pdef is not None
    assert (pdef.id, pdef.source, pdef.auth_type) == (PROVIDER, 'plugin-profile', 'external_process')
    assert pdef.base_url == f'process://{PROVIDER}'


def test_switch_with_explicit_provider(switch_env):
    result = _switch(f'gemini-3.8-flash-low --provider {PROVIDER}')
    assert result.success, result.error_message
    assert result.target_provider == PROVIDER
    assert result.new_model == 'gemini-3.8-flash-low'
    assert result.base_url == f'process://{PROVIDER}'
    assert result.api_mode == 'chat_completions'
    assert result.provider_changed
    assert not result.warning_message


def test_switch_with_provider_alias(switch_env):
    result = _switch(f'flash --provider {PROVIDER}')
    assert result.success, result.error_message
    assert (result.target_provider, result.new_model) == (PROVIDER, 'gemini-3.8-flash-medium')


def test_switch_within_provider_by_alias_and_slug(switch_env):
    for typed, expected in (('pro', 'gemini-3.1-pro-high'), ('claude-sonnet-4-6', 'claude-sonnet-4-6'),
                            ('gemini-3.1-pro-low', 'gemini-3.1-pro-low')):
        result = _switch(typed, current_provider=PROVIDER, current_model='gemini-3.8-flash-low')
        assert result.success, result.error_message
        assert (result.target_provider, result.new_model) == (PROVIDER, expected)
        assert not result.provider_changed


def test_switch_with_provider_is_session_scoped(switch_env, tmp_path):
    # ``--provider`` without ``--global`` never writes config.yaml once a default model exists.
    import os
    from pathlib import Path
    from hermes_cli.model_switch import resolve_persist_behavior
    config = Path(os.environ['HERMES_HOME']) / 'config.yaml'
    config.write_text('model:\n  default: gpt-5\n  provider: openai-codex\nplugins:\n  enabled: []\n',
                      encoding='utf-8')
    assert resolve_persist_behavior(False, False, explicit_provider=PROVIDER) is False


def test_capabilities_hide_noop_reasoning_step(switch_env):
    from agent.models_dev import get_model_capabilities, lookup_models_dev_context
    caps = get_model_capabilities(PROVIDER, 'gemini-3.8-flash-low')
    assert caps is not None
    assert caps.supports_reasoning is False
    assert caps.supports_tools is True
    assert caps.supports_vision is None  # undeclared: image routing unchanged
    assert caps.context_window == 1_000_000
    assert lookup_models_dev_context(PROVIDER, 'claude-sonnet-4-6') == 200_000
    # The picker's effort step reads the inventory capability map built from these declarations.
    from hermes_cli.cli_model_switch_mixin import _picker_offers_reasoning
    from hermes_cli.inventory import _apply_capabilities
    rows = [{'slug': PROVIDER, 'models': ['gemini-3.8-flash-low', 'brand-new-model']}]
    _apply_capabilities(rows)
    assert _picker_offers_reasoning(rows[0], 'gemini-3.8-flash-low') is False
    # Undeclared (live-only) slugs keep Hermes' default rather than a guess.
    assert _picker_offers_reasoning(rows[0], 'brand-new-model') is True
