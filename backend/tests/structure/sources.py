"""Minimal, deterministic file contents for the structural enricher
suite. Line numbers are load-bearing: tests assert spans against these
exact bytes. httpx/__init__.py exists so `import httpx` is resolvable
in-corpus; httpx/_config.py is imported by _client.py; ssl is external."""

from __future__ import annotations

CLIENT_PY = '''import ssl

import httpx
from httpx._config import DEFAULT_TIMEOUT_CONFIG, DEFAULT_LIMITS

DEFAULT_RETRIES = 3


class Client:
    retry_count = 0

    def _send_handling_redirects(self, method, url):
        allow_redirects = True
        return allow_redirects
'''

CONFIG_PY = '''DEFAULT_TIMEOUT_CONFIG = (3, 10)
DEFAULT_LIMITS = {"max_keepalive_connections": 100}
'''

INIT_PY = '__all__ = ["Client", "AsyncClient"]\n'

BROKEN_PY = 'def broken(:\n    this is not python\n'

LIMITS_TOML = """[httpx]
max_keepalive_connections = 20
max_connections = 100
"""

LOGGING_YAML = """logging:
  level: INFO
"""

ENV_EXAMPLE = "HTTPX_TIMEOUT=30\nAPI_BASE=...\n"

API_MD = '# API\nThe timeout env HTTPX_TIMEOUT controls requests.\n'

SOURCES: dict[str, str] = {
    "httpx/_client.py": CLIENT_PY,
    "httpx/_config.py": CONFIG_PY,
    "httpx/__init__.py": INIT_PY,
    "broken/syntax.py": BROKEN_PY,
    "config/limits.toml": LIMITS_TOML,
    "config/logging.yaml": LOGGING_YAML,
    ".env.example": ENV_EXAMPLE,
    "assets/logo.png": "\x00\x01\x02\x03",
}

SYMBOL_ID = {
    "client": "httpx:symbol:httpx/_client.py:Client",
    "redirects": "httpx:symbol:httpx/_client.py:Client._send_handling_redirects",
    "retries": "httpx:symbol:httpx/_client.py:DEFAULT_RETRIES",
    "timeout": "httpx:symbol:httpx/_config.py:DEFAULT_TIMEOUT_CONFIG",
    "limits": "httpx:symbol:httpx/_config.py:DEFAULT_LIMITS",
}

CONFIG_KEY_IDS = {
    "keepalive": "httpx:config:config/limits.toml:httpx.max_keepalive_connections",
    "maxconn": "httpx:config:config/limits.toml:httpx.max_connections",
    "loglevel": "httpx:config:config/logging.yaml:logging.level",
    "envtimeout": "httpx:config:.env.example:HTTPX_TIMEOUT",
}

EXTERNAL_SSL_ID = "httpx:ext:ssl"
