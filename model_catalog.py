"""Fallback catalog and context windows for agy model slugs.

The live list comes from ``agy models`` (see ``agy_setup.discover_models``); this table only
supplies the picker when the CLI cannot be queried, plus a context window per family so Hermes
never has to guess one. Visibility is never filtered down to this table.
"""
FALLBACK_MODELS = (
    'gemini-3.8-flash-low',
    'gemini-3.8-flash-medium',
    'gemini-3.8-flash-high',
    'gemini-3.7-flash-low',
    'gemini-3.7-flash-medium',
    'gemini-3.7-flash-high',
    'gemini-3.6-flash-low',
    'gemini-3.6-flash-medium',
    'gemini-3.6-flash-high',
    'gemini-3.1-pro-low',
    'gemini-3.1-pro-high',
    'claude-sonnet-4-6',
    'claude-opus-4-6-thinking',
    'gpt-oss-120b-medium',
)

DEFAULT_MODEL = 'gemini-3.8-flash-low'

ALIASES = {
    'flash': 'gemini-3.8-flash-medium',
    'pro': 'gemini-3.1-pro-high',
    'sonnet': 'claude-sonnet-4-6',
    'opus': 'claude-opus-4-6-thinking',
}

# Family prefix -> window. Conservative: agy's harness limits are not published, and the whole
# Hermes history travels as one agy user message, so Hermes must compact before these.
_WINDOWS = (
    ('gemini-', 1_000_000),
    ('claude-', 200_000),
    ('gpt-oss-', 128_000),
)
DEFAULT_WINDOW = 128_000


# agy refuses a bare family id ("--model gemini-3.8-flash requires --effort"); pick the variant the
# aliases already use for a family (medium, else high, else low).
_BARE_FAMILY_EFFORTS = ('medium', 'high', 'low')


def native_model(model):
    """Hermes route id -> agy ``--model`` slug."""
    model = (model or '').strip()
    model = ALIASES.get(model, model)
    if model not in FALLBACK_MODELS:
        for effort in _BARE_FAMILY_EFFORTS:
            if f'{model}-{effort}' in FALLBACK_MODELS:
                return f'{model}-{effort}'
    return model


def context_window(model):
    slug = native_model(model)
    for prefix, window in _WINDOWS:
        if slug.startswith(prefix):
            return window
    return DEFAULT_WINDOW


def model_capabilities():
    """``ProviderProfile.model_capabilities`` for the declared slugs (Hermes ``model_overrides`` schema).

    ``supports_reasoning: False`` because effort is baked into the slug and the transport ignores
    ``reasoning_effort``: it hides the no-op effort step in the ``/model`` picker. Vision stays
    undeclared so Hermes' image routing keeps its current behaviour.
    """
    return {slug: {'context_window': context_window(slug), 'supports_tools': True, 'supports_reasoning': False}
            for slug in FALLBACK_MODELS}
