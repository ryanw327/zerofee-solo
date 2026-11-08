"""Discover an existing restricted Core session without reading credentials."""
import os
from pathlib import Path


def discover_session(datadir, session_root=None):
    from endpoint import endpoints, verify_endpoint
    rows = endpoints(19732)
    if not rows:
        raise ValueError('Bitcoin Core has no mining connection on this PC. '
                         'Its normal startup has RPC off. A new temporary mining session must be enabled first.')
    if len(rows) != 1 or rows[0][0] != '127.0.0.1':
        raise ValueError('Core mining connection has an unexpected binding. Session setup needs review.')
    base = Path(session_root) if session_root else Path(os.environ['LOCALAPPDATA']) / 'BitcoinPreflight'
    cookies = list(base.glob('*/.cookie'))
    if len(cookies) != 1:
        raise ValueError('No unique private Core session was found. Session setup needs review.')
    cookie = cookies[0].absolute()
    pid = rows[0][1]
    verify_endpoint(pid, str(cookie), 19732, datadir)
    return dict(cookie=str(cookie), core_pid=pid)
