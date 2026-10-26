"""Local-only first-run guard.

The first-run setup form exists only to initialise the fixed local demo
identity. It must not become a public registration form: the supported
launcher binds Streamlit to 127.0.0.1, and this module additionally refuses
to show the setup entry for non-loopback Host headers (for example when
someone deliberately exposes the development server on a LAN interface or
behind a reverse proxy).

The check is a defence-in-depth boundary, not a replacement for the local
operating-system account boundary. Nothing here grants access to bank money
or approves a payment.
"""

_LOOPBACK_NAMES = frozenset({'127.0.0.1', 'localhost', '::1', '0:0:0:0:0:0:0:1'})


def setup_host_allowed(host):
    """Return True only for a Host header that names the local machine."""
    if not isinstance(host, str):
        return False
    value = host.strip().lower()
    if not value or any(ch in value for ch in (' ', '/', '\\', '@', ',')):
        return False
    if value.startswith('['):
        end = value.find(']')
        if end < 0:
            return False
        base, rest = value[1:end], value[end + 1:]
        if rest and not (rest.startswith(':') and rest[1:].isdigit()):
            return False
        return base in _LOOPBACK_NAMES
    if value.count(':') == 1:
        base, port = value.rsplit(':', 1)
        if not port.isdigit():
            return False
        return base in _LOOPBACK_NAMES
    if ':' in value:  # bare IPv6 without a port
        return value in _LOOPBACK_NAMES
    return value in _LOOPBACK_NAMES


def setup_allowed(headers=None, *, server_address=None):
    """Setup requires a trusted loopback listener and a loopback Host.

    Host alone is client-controlled. Unknown listener/header information is
    insufficient to initialise the existing local bank identity.
    """
    if server_address not in _LOOPBACK_NAMES or headers is None:
        return False
    try:
        host = headers.get('Host') or headers.get('host')
    except Exception:
        return False
    return setup_host_allowed(host)
