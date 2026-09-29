"""Offline test process guard, activated by CI's PYTHONPATH=.:tests.

Runs before unittest imports application code. Vendor SDK boundaries are faked
by tests; the native Codex adversarial test may still use a loopback server.
"""

import errno
import ipaddress
import os
import socket


_SECRET_NAMES = {
    "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN",
}
_SECRET_PREFIXES = ("JIRA_", "ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "GOOGLE_CLOUD_")
for _key in tuple(os.environ):
    if _key in _SECRET_NAMES or _key.startswith(_SECRET_PREFIXES):
        os.environ.pop(_key, None)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def _loopback(host):
    if host is None:
        return True
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_getaddrinfo = socket.getaddrinfo
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _offline_getaddrinfo(host, port, *args, **kwargs):
    if not _loopback(host):
        raise OSError(errno.ENETUNREACH, "External network disabled for tests")
    return _getaddrinfo(host, port, *args, **kwargs)


def _offline_connect(sock, address):
    if isinstance(address, tuple) and not _loopback(address[0]):
        raise OSError(errno.ENETUNREACH, "External network disabled for tests")
    return _connect(sock, address)


def _offline_connect_ex(sock, address):
    if isinstance(address, tuple) and not _loopback(address[0]):
        return errno.ENETUNREACH
    return _connect_ex(sock, address)


socket.getaddrinfo = _offline_getaddrinfo
socket.socket.connect = _offline_connect
socket.socket.connect_ex = _offline_connect_ex
