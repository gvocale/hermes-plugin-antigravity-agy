import agy_setup
import model_catalog


def test_discover_models_parses_agy_models(fake_agy):
    command, env, _, _ = fake_agy
    rows = agy_setup.discover_models(command=[command], env=env)
    assert [r['id'] for r in rows] == ['gemini-3.8-flash-low', 'gemini-3.1-pro-high', 'claude-sonnet-4-6']
    assert rows[0]['label'] == 'Gemini 3.8 Flash (Low)'


def test_setup_status_signed_in(fake_agy):
    command, env, _, _ = fake_agy
    status = agy_setup.setup_status(command=[command], env=env)
    assert status['available'] and status['logged_in']
    assert 'Weekly Limit Remaining: 89%' in status['detail']


def test_setup_status_logged_out(fake_agy):
    command, env, _, set_scenario = fake_agy
    set_scenario('logged_out')
    status = agy_setup.setup_status(command=[command], env=env)
    assert status['available'] and not status['logged_in']
    assert 'not signed in' in status['detail']


def test_setup_status_missing(tmp_path):
    status = agy_setup.setup_status(command=[str(tmp_path / 'nope')], env={'PATH': str(tmp_path)})
    assert status == {'available': False, 'logged_in': False, 'plan': '', 'detail': agy_setup.INSTALL_HINT,
                      'login_command': None}
    assert agy_setup.discover_models(command=[str(tmp_path / 'nope')], env={'PATH': str(tmp_path)}) is None


def test_context_windows():
    assert model_catalog.context_window('gemini-3.8-flash-low') == 1_000_000
    assert model_catalog.context_window('claude-sonnet-4-6') == 200_000
    assert model_catalog.context_window('gpt-oss-120b-medium') == 128_000
    assert model_catalog.context_window('brand-new-model') == model_catalog.DEFAULT_WINDOW
    assert model_catalog.context_window('opus') == 200_000


def test_profile_discovered_from_hermes_home(profile):
    assert profile.name == 'antigravity-agy'
    assert profile.auth_type == 'external_process'
    assert profile.api_mode == 'chat_completions'
    assert profile.process_command == 'agy'
    assert profile.process_command_env_vars == ('ANTIGRAVITY_AGY_COMMAND',)
    assert 'gemini-3.8-flash-low' in profile.fallback_models
    assert profile.get_model_context_length('gemini-3.1-pro-high') == 1_000_000
    assert profile.model_aliases['sonnet'] == 'claude-sonnet-4-6'


def test_profile_client_and_missing_cli(profile, tmp_path):
    client = profile.create_client(api_key='antigravity-agy', base_url='process://antigravity-agy',
                                   command=str(tmp_path / 'no-such-agy'), args=[], unknown_future_kwarg=1)
    assert type(client).__name__ == 'Client'
    assert client.base_url == 'process://antigravity-agy'
    # Missing CLI: discovery degrades to the fallback catalog instead of raising.
    assert profile.fetch_models() is None
    assert profile.setup_status()['available'] is False


def test_profile_declares_model_capabilities(profile):
    caps = profile.model_capabilities
    assert set(caps) == set(profile.fallback_models)
    assert caps['gemini-3.8-flash-low'] == {'context_window': 1_000_000, 'supports_tools': True,
                                            'supports_reasoning': False}
    assert caps['gpt-oss-120b-medium']['context_window'] == 128_000
    # Vision stays undeclared so Hermes' image routing is unchanged.
    assert not any('supports_vision' in entry for entry in caps.values())


def test_bare_family_slug_maps_to_a_real_variant():
    assert model_catalog.native_model('gemini-3.8-flash') == 'gemini-3.8-flash-medium'
    assert model_catalog.native_model('gemini-3.1-pro') == 'gemini-3.1-pro-high'
    assert model_catalog.native_model('gemini-3.8-flash-low') == 'gemini-3.8-flash-low'
    assert model_catalog.native_model('claude-sonnet-4-6') == 'claude-sonnet-4-6'
    assert model_catalog.native_model('brand-new-model') == 'brand-new-model'
