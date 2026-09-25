"""Antigravity (agy) — Hermes model-provider registration.

Hermes talks to the official ``agy`` CLI in print mode, on the user's Google Antigravity
subscription. ``transport.py`` is the request path, ``inert_mcp.py`` the tool inventory agy sees,
``agy_setup.py`` the offline setup probes and ``model_catalog.py`` the fallback catalog.
"""
import logging
import os

from providers import register_provider
from providers.base import ProviderProfile

# Dual import: the Hermes loader imports this directory as a package; the flat test path does not.
try:
    from .model_catalog import (ALIASES, DEFAULT_MODEL, FALLBACK_MODELS, context_window, model_capabilities,
                                native_model)
    from .agy_setup import COMMAND_ENV, INSTALL_HINT, _resolve
except ImportError:
    from model_catalog import (ALIASES, DEFAULT_MODEL, FALLBACK_MODELS, context_window, model_capabilities,
                               native_model)
    from agy_setup import COMMAND_ENV, INSTALL_HINT, _resolve

logger = logging.getLogger(__name__)

PROVIDER = 'antigravity-agy'


class AntigravityAgyProfile(ProviderProfile):
    def get_model_context_length(self, model):
        return context_window(model)

    def create_client(self, **client_kwargs):
        try:
            from .transport import Client
        except ImportError:
            from transport import Client
        return Client(**client_kwargs)

    def fetch_models(self, **kwargs):
        rows = self.discover_models(**kwargs)
        return [row['id'] for row in rows] if rows else None

    def setup_status(self, **kwargs):
        try:
            from .agy_setup import setup_status
        except ImportError:
            from agy_setup import setup_status
        return setup_status(**{k: v for k, v in kwargs.items() if k in ('command', 'env', 'timeout')})

    def discover_models(self, **kwargs):
        try:
            from .agy_setup import discover_models
        except ImportError:
            from agy_setup import discover_models
        return discover_models(**{k: v for k, v in kwargs.items() if k in ('command', 'env', 'timeout')})

    def build_api_kwargs_extras(self, **_):
        # Effort is part of the agy model slug (``-low``/``-high``); nothing to add on the wire.
        return {}, {}


profile = AntigravityAgyProfile(
    name=PROVIDER,
    display_name='Antigravity (agy CLI)',
    description='Google Antigravity subscription via your signed-in agy CLI; Hermes owns tools',
    api_mode='chat_completions',
    auth_type='external_process',
    supports_health_check=False,
    env_vars=(),
    base_url=f'process://{PROVIDER}',
    process_command='agy',
    process_args=(),
    process_command_env_vars=(COMMAND_ENV,),
    default_aux_model=DEFAULT_MODEL,
    fallback_models=FALLBACK_MODELS,
    model_aliases={alias: native_model(alias) for alias in ALIASES},
    model_capabilities=model_capabilities(),
)
register_provider(profile)

# Stay registered when agy is missing so `hermes model` can show the install hint; the request
# path refuses with the same message.
if _resolve(None, os.environ) is None:
    logger.warning('%s: %s', profile.display_name, INSTALL_HINT)
