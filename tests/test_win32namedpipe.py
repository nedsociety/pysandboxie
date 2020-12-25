import sandboxie.win32namedpipe as w32np
import pytest
import pywintypes
import io
import threading
import time


def test_basic_inbound():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    assert server.name == client.name

    with server.accept(skip_connection_wait=True) as sf:
        with client.connect() as cf:
            server.wait_for_connection(sf)

            cf.write(b'test')
            cf.flush()
            assert sf.read(4) == b'test'


def test_basic_outbound():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=False, outbound=True)
    client = w32np.Win32NamedPipeClient(server.name, inbound=True, outbound=False)

    with server.accept(skip_connection_wait=True) as sf:
        with client.connect() as cf:
            server.wait_for_connection(sf)

            sf.write(b'test')
            sf.flush()
            assert cf.read(4) == b'test'


def test_basic_duplex():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=True)
    client = w32np.Win32NamedPipeClient(server.name, inbound=True, outbound=True)

    with server.accept(skip_connection_wait=True) as sf:
        with client.connect() as cf:
            server.wait_for_connection(sf)

            sf.write(b'test')
            sf.flush()
            cf.write(b'test')
            cf.flush()
            assert sf.read(4) == b'test'
            assert cf.read(4) == b'test'


def test_invalid_options():
    with pytest.raises(ValueError):
        w32np.Win32NamedPipeServer(r'\\.\pipe\testpipe', inbound=False, outbound=False)
    with pytest.raises(ValueError):
        w32np.Win32NamedPipeClient(r'\\.\pipe\testpipe', inbound=False, outbound=False)


def test_invalid_wrapping():
    with pytest.raises(OSError):
        w32np._wrap_win32_handle_to_file(pywintypes.HANDLE(0), 0, 'rb', 0)


def test_brokenpipe():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    with pytest.raises(BrokenPipeError):
        sf = server.accept(skip_connection_wait=True)
        cf = client.connect()
        server.wait_for_connection(sf)

        sf.close()
        try:
            cf.write(b'test')
            cf.flush()
        finally:
            cf.close()


def test_unseekable():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    with pytest.raises(io.UnsupportedOperation):
        with server.accept(skip_connection_wait=True) as sf:
            with client.connect() as cf:
                server.wait_for_connection(sf)

                cf.seek(42)


def test_accept_wait():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    def threadfunc():
        time.sleep(2)
        with client.connect() as cf:
            cf.write(b'test')
            cf.close()

    th = threading.Thread(target=threadfunc)
    th.start()
    with server.accept() as sf:
        assert sf.read(4) == b'test'

    th.join()


def test_busy_pipe():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    def threadfunc():
        with client.connect(wait_timeout_ms=-1) as cf2:
            time.sleep(2) # This is required to prevent BrokenPipeError on server side

    with server.accept(skip_connection_wait=True) as sf:
        with client.connect() as cf:
            server.wait_for_connection(sf)

            with pytest.raises(TimeoutError):
                cf2 = client.connect()

            th = threading.Thread(target=threadfunc)
            th.start()

            time.sleep(2)
            with server.accept() as sf2:
                th.join()

def test_connection_broken_at_start():
    server = w32np.temppipeserver(r'\\.\pipe\testpipe', inbound=True, outbound=False)
    client = w32np.Win32NamedPipeClient(server.name, inbound=False, outbound=True)

    with server.accept(skip_connection_wait=True) as sf:
        with client.connect() as cf:
            pass

        with pytest.raises(BrokenPipeError):
            server.wait_for_connection(sf)

def test_pipepath_unc_to_nt_namespace():
    assert w32np.pipepath_unc_to_nt_namespace(r'\\.\pipe\testname') == r'\Device\NamedPipe\testname'
    with pytest.raises(ValueError):
        w32np.pipepath_unc_to_nt_namespace(r'\\.\nonpipe\testname')
