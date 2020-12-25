import win32pipe
import win32api
import win32file
import winerror
import pywintypes
import typing
import msvcrt
import os
import io
import time
import functools
import re

# This pipe implementation works only for the most basic cases:
# - Blocking only
# - No write-through mode
# - No security features
# - Byte stream only
# - Unlimited instance
# - Local only


def _translate_exception(target):
    @functools.wraps(target)
    def wrapped(*args, **kwargs):
        try:
            return target(*args, **kwargs)
        except OSError as e:
            if e.errno == 22:
                raise BrokenPipeError from e
            else:
                raise
    return wrapped


def _unsupported_seek(target):
    @functools.wraps(target)
    def wrapped(*args, **kwargs):
        raise io.UnsupportedOperation('underlying stream is not seekable')
    return wrapped


def _monkeypatch_stream(f):
    # Translate OSError(22) to BrokenPipeError
    # See https://bugs.python.org/issue35754
    for method in (
        'close', 'flush', 'readline', 'readlines', 'writelines',  # io.IOBase
        'read', 'readall', 'readinto', 'write',  # io.RawIOBase
        # 'read1', 'readinto1' # io.BufferedIOBase methods defer to the read() and readinto() methods
    ):
        target = getattr(f, method, None)
        if target is None:
            continue

        setattr(f, method, _translate_exception(target))

    # Disable seeking operations
    # See https://bugs.python.org/issue42602
    for method in (
        'seek', 'tell', 'truncate'  # io.IOBase
    ):
        target = getattr(f, method, None)
        if target is None:
            continue  # coverage: no cover

        setattr(f, method, _unsupported_seek(target))
    setattr(f, 'seekable', lambda: False)

    return f


def _wrap_win32_handle_to_file(pyhandle, open_osfhandle_flags, fdopen_mode, buffering):
    handle_value = pyhandle.Detach()
    try:
        fd = msvcrt.open_osfhandle(handle_value, open_osfhandle_flags)
    except:
        win32api.CloseHandle(handle_value)
        raise

    try:
        return _monkeypatch_stream(open(fd, fdopen_mode, buffering=buffering))
    except:  # coverage: no cover
        os.close(fd)
        raise

__all__ = (
    'Win32NamedPipeServer', 'Win32NamedPipeClient', 'temppipeserver', 'pipepath_unc_to_nt_namespace'
)


class Win32NamedPipeServer:
    def __init__(
        self, name: str, inbound: bool = True, outbound: bool = True, buffering: int = -1,
        buffer_size: int = io.DEFAULT_BUFFER_SIZE, default_client_wait_timeout_ms: int = 0,
        sa: typing.Optional[pywintypes.SECURITY_ATTRIBUTES] = None  # pylint: disable=no-member,unsubscriptable-object
    ):
        r'''
        Create a named pipe object for server.

        :param name: Name of the pipe. Must have the following form of `\\.\pipe\pipename` where `pipename` can be
                     include any character excluding backslash. `name` can be at most 256 characters long and is not
                     case sensitive.
        :type name: str
        :param inbound: Specifies whether the pipe should be readable from server side.
        :type inbound: bool, optional
        :param outbound: Specifies whether the pipe should be writable from server side.
        :type outbound: bool, optional
        :param buffering: Determines the buffering behavior for the resulting file object, as if it is passed to the
                          `open()` call.
        :type buffering: int, optional
        :param buffer_size: Size of the internal buffer, defaults to io.DEFAULT_BUFFER_SIZE. Not to be confused with
                            `buffering` parameter which determines the buffering behavior of the resulting file, not the
                            pipe itself.
        :type buffer_size: int, optional
        :param default_client_wait_timeout_ms: Default timeout applied to clients when they try to connect. This value
                                               is merely suggestive; they may ignore this value and use their desired
                                               timeout instead. Not specifying this argument or specifying 0 result in
                                               default OS timeout, which is 50 ms.
        :type default_client_wait_timeout_ms: int, optional
        :param sa: Security attributes to the created pipe.
        :type default_client_wait_timeout_ms: typing.Optional[pywintypes.SECURITY_ATTRIBUTES], optional
        '''
        self._name = name
        self._buffering = buffering
        self._sa = sa
        self._pipemode = win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT
        self._buffer_size = buffer_size
        self._default_client_wait_timeout_ms = default_client_wait_timeout_ms
        if inbound and outbound:
            self._openmode = win32pipe.PIPE_ACCESS_DUPLEX
            self._open_osfhandle_flags = 0
            self._fdopen_mode = 'r+b'
        elif inbound:
            self._openmode = win32pipe.PIPE_ACCESS_INBOUND
            self._open_osfhandle_flags = os.O_RDONLY
            self._fdopen_mode = 'rb'
        elif outbound:
            self._openmode = win32pipe.PIPE_ACCESS_OUTBOUND
            self._open_osfhandle_flags = 0
            self._fdopen_mode = 'wb'
        else:
            raise ValueError('invalid inbound and outbound combination')

    @property
    def name(self):
        return self._name

    def accept(self, *, skip_connection_wait: bool = False) -> typing.BinaryIO:
        '''
        Connect to a client and wrap it into Python file object.

        :return: A binary file object for the connected pipe.
        :rtype: typing.BinaryIO
        '''
        handle = win32pipe.CreateNamedPipe(
            self._name, self._openmode, self._pipemode, win32pipe.PIPE_UNLIMITED_INSTANCES,
            self._buffer_size, self._buffer_size, self._default_client_wait_timeout_ms, self._sa
        )

        pipefile = _wrap_win32_handle_to_file(handle, self._open_osfhandle_flags, self._fdopen_mode, self._buffering)

        if not skip_connection_wait:
            try:
                self.wait_for_connection(pipefile)
            except:  # coverage: no cover
                pipefile.close()
                raise

        return pipefile

    def wait_for_connection(self, pipefile: typing.BinaryIO):
        handle = msvcrt.get_osfhandle(pipefile.fileno())
        try:
            hr = win32pipe.ConnectNamedPipe(handle, None)
            if hr not in (0, winerror.ERROR_PIPE_CONNECTED):
                raise OSError(hr)  # coverage: no cover
        except pywintypes.error as e:  # pylint: disable=no-member
            if e.winerror == winerror.ERROR_NO_DATA:
                raise BrokenPipeError('client connected but immediately closed the connection') from e
            raise


class Win32NamedPipeClient:
    def __init__(
        self, name: str, inbound: bool = True, outbound: bool = True, buffering: int = -1,
    ):
        '''
        Create a named pipe object for client.

        :param name: Name of the pipe. Must match the name of the pipe created by the server.
        :type name: str
        :param inbound: Specifies whether the pipe should be readable from client side. Must match the `outbound`
                        argument specified by the server.
        :type inbound: bool, optional
        :param outbound: Specifies whether the pipe should be writable from client side. Must match the `inbound`
                         argument specified by the server.
        :type outbound: bool, optional
        :param buffering: Determines the buffering behavior for the resulting file object, as if it is passed to the
                          `open()` call.
        :type buffering: int, optional
        '''
        self._name = name
        self._buffering = buffering
        self._createfile_access = 0
        if inbound and outbound:
            self._createfile_access = win32file.GENERIC_READ | win32file.GENERIC_WRITE
            self._open_osfhandle_flags = 0
            self._fdopen_mode = 'r+b'
        elif inbound:
            self._createfile_access = win32file.GENERIC_READ
            self._open_osfhandle_flags = os.O_RDONLY
            self._fdopen_mode = 'rb'
        elif outbound:
            self._createfile_access = win32file.GENERIC_WRITE
            self._open_osfhandle_flags = 0
            self._fdopen_mode = 'wb'
        else:
            raise ValueError('invalid inbound and outbound combination')

    @property
    def name(self):
        return self._name

    def connect(self, wait_timeout_ms: int = 0) -> typing.BinaryIO:
        '''
        Connect to the server and wrap it into Python file object.

        :param wait_timeout_ms: Timeout value in milliseconds. If 0 (default) is provided, it will use the value
                                suggested by the server. If -1 is provided, it will wait indefinitely.
        :type wait_timeout_ms: int, optional
        :return: A binary file object for the connected pipe.
        :rtype: typing.BinaryIO
        '''
        while True:
            try:
                handle = win32file.CreateFile(
                    self._name, self._createfile_access, 0, None, win32file.OPEN_EXISTING, 0, None
                )
                break
            except pywintypes.error as e:  # pylint: disable=no-member
                if e.winerror != winerror.ERROR_PIPE_BUSY:
                    raise  # coverage: no cover

                if wait_timeout_ms == 0:
                    wait_timeout_ms = win32pipe.NMPWAIT_USE_DEFAULT_WAIT
                elif wait_timeout_ms == -1:
                    wait_timeout_ms = win32pipe.NMPWAIT_WAIT_FOREVER

                try:
                    win32pipe.WaitNamedPipe(self._name, wait_timeout_ms)
                except pywintypes.error as e:  # pylint: disable=no-member
                    if e.winerror == winerror.ERROR_SEM_TIMEOUT:
                        raise TimeoutError from None
                    raise

        return _wrap_win32_handle_to_file(handle, self._open_osfhandle_flags, self._fdopen_mode, self._buffering)

_TEMPPIPECOUNT = 0

def temppipeserver(prefix, *args, **kwargs):
    global _TEMPPIPECOUNT
    _TEMPPIPECOUNT += 1
    return Win32NamedPipeServer(f'{prefix}_{time.time_ns()}_{_TEMPPIPECOUNT}', *args, **kwargs)


def pipepath_unc_to_nt_namespace(name: str) -> str:
    r'''
    Convert a UNC path to a local named pipe (prefixed by `\\.\pipe\`) into NT namespace equivalent (prefixed by
    `\Device\NamedPipe\`).

    :param name: UNC path to a local named pipe.
    :type name: str
    :return: NT namespace path to a local named pipe.
    :rtype: str
    '''
    match = re.fullmatch(r'\\\\.\\pipe\\(.*)', name)
    if match is None:
        raise ValueError('name is not a proper UNC path to a local named pipe.')
    return rf'\Device\NamedPipe\{match[1]}'