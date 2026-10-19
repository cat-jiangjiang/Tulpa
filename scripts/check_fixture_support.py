"""Shared isolated fixtures, independent of the optional terminal UI."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = frozenset({'127.0.0.1', 'localhost', '::1'})


def isolated_env():
    """Clear user configuration while retaining Windows TLS/runtime roots."""
    return {key: value for key, value in os.environ.items()
            if key.upper() in {'SYSTEMROOT', 'WINDIR'}}


@contextmanager
def temp_dir(prefix='tulpa-test-'):
    """Scratch directory for fixtures, isolated from the working tree."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def network_guard(ports):
    """Patch helper: allow loopback connections to ``ports`` only.

    Returns ``(restrict, calls)`` so the caller can install ``restrict`` over
    ``socket.create_connection`` and later assert on ``calls``.
    """
    allowed = {int(port) for port in ports}
    real_connect = socket.create_connection
    calls = []

    def restrict(address, *args, **kwargs):
        assert address[0] in LOOPBACK and int(address[1]) in allowed, ('non-fixture connection blocked', address)
        calls.append(address)
        return real_connect(address, *args, **kwargs)

    return restrict, calls


@contextmanager
def management_http(app):
    import uvicorn
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    # TestClient owns application startup/shutdown in the caller.
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and time.monotonic() < deadline: time.sleep(.02)
        if not server.started: raise AssertionError('Management HTTP did not start')
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(5); sock.close()
        if thread.is_alive(): raise AssertionError('Management HTTP did not stop')
