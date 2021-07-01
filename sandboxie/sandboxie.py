import textwrap
import re
import subprocess
import ctypes
import winreg
import sys
import typing
import os
from pathlib import Path
from . import win32namedpipe


__all__ = ('SandboxiePipedProcess', 'Sandboxie')



class SandboxiePipedProcess:
    '''
    Represents the running child process inside Sandboxie, and provides following similar to that of `subprocess.Popen`:
        - Standard handles to the child (`.stdin`, `.stdout`, `.stderr`)
        - Context manager protocol for automatically closing the child and waiting for its closing
        - Retrieving the `returncode`
    '''

    def __init__(self, popen, stdin, stdout, stderr):
        self._popen = popen
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr

    @property
    def stdin(self):
        return self._stdin

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr

    def __enter__(self):
        return self

    def __exit__(self, exc_type, value, traceback):
        if self.stdout:
            self.stdout.close()
        if self.stderr:
            self.stderr.close()
        try:  # Flushing a BufferedWriter may raise an error
            if self.stdin:
                self.stdin.close()
        except BrokenPipeError:
            pass
        finally:
            if exc_type != KeyboardInterrupt:
                # Wait for the process to terminate, to avoid zombies.
                self.wait()

    def wait(self, timeout=None):
        return self._popen.wait(timeout=timeout)

    @property
    def returncode(self):
        return self._popen.returncode


class Sandboxie:
    '''
    Represents the Sandboxie application.
    '''
    _PIPE_PREFIX = r'\\.\pipe\pysandboxie_pipe'

    SETTING_TEMPLATES = {
        'default': list(filter(None, textwrap.dedent(
            # Note: These default settings are not complete -- in most cases the SbieCtrl.exe will pop-up a new list of
            #       compatibility templates when it restarts. As we won't implement all the Template.ini detection
            #       logic, we'll just go for a minimal setup instead.
            r'''
            Enabled=y
            ConfigLevel=7
            BlockNetworkFiles=y
            Template=WindowsFontCache
            Template=BlockPorts
            Template=LingerPrograms
            BorderColor=#00FFFF,ttl
            '''
        ).split('\n'))),
        'piped_execution': [rf'OpenPipePath={win32namedpipe.pipepath_unc_to_nt_namespace(_PIPE_PREFIX)}*']
    }
    
    DEFAULTBOX = 'DefaultBox'

    def _locate_start(self):  # coverage: no cover
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services\SbieSvc') as hreg:
                svc_imagepath, type = winreg.QueryValueEx(hreg, "ImagePath")
                if type == winreg.REG_SZ:
                    pass
                elif type == winreg.REG_EXPAND_SZ:
                    svc_imagepath = winreg.ExpandEnvironmentStrings(svc_imagepath)
                else:
                    raise OSError
                svc_imagepath = svc_imagepath.strip('"')

                startpath = Path(svc_imagepath).parent / 'Start.exe'
        except OSError:
            # Fallback
            startpath = Path(r'C:\Program Files\Sandboxie\Start.exe')

        if not startpath.is_file():
            raise FileNotFoundError('cannot locate sandboxie installation (Start.exe)')

        self._startpath = startpath

    def _locate_ini(self):  # coverage: no cover
        inipath = Path(winreg.ExpandEnvironmentStrings(r'%windir%\Sandboxie.ini'))
        if not inipath.exists():
            inipath = self._startpath.parent / 'Sandboxie.ini'
        if not inipath.exists():
            raise FileNotFoundError('cannot locate sandboxie configuration (Sandboxie.ini)')

        self._inipath = inipath

    def _require_uac_admin(self): # coverage: no cover
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError('This operation requires UAC admin right')

    def _readini(self):
        # The Sandboxie.ini file is inherently different from ordinary INI files and thus cannot use ConfigParser:
        # - It may have multiple entries with same key
        # - It is newline-sensitive -- SbieCtrl sometimes lose information if the newlines are not properly set.

        ret = {}

        currentsection = None

        with self._inipath.open('r', encoding='utf-16-le') as f:
            # In case someone saved it with BOM
            if f.read(1) != '\ufeff':
                f.seek(0)

            for line in f:
                line = line.strip('\r\n')
                if not line:
                    continue

                match = re.fullmatch(r'^\[(.*)\]$', line)
                if match:
                    currentsection = match[1]
                    ret[currentsection] = []
                else:
                    assert currentsection is not None
                    ret[currentsection].append(line)

        return ret

    def _writeini(self, content_dict: dict[str, list[str]]):
        self._require_uac_admin()

        with self._inipath.open('w', encoding='utf-16-le', newline='\r\n') as f:
            for section, lines in content_dict.items():
                f.write(f'\n[{section}]\n\n')
                for line in lines:
                    f.write(f'{line}\n')

    def _reloadini(self):
        return subprocess.run([self._startpath, '/reload'], check=True)

    def __init__(self):
        self._locate_start()
        self._locate_ini()
        self._subprocess_debugging = False

    def enable_subprocess_debugging(self, enable: bool): # coverage: no cover
        '''
        Enables or disables debugpy debugging under sandboxed subprocesses. Enabling this feature requires current
        process under debugging by debugpy. Otherwise this function will raise NotImplementedError.

        Note: Calling this function with `enable=True` **breaks** coverage.py, even if the process is not under
              debugging. Do not use this function when running coverage.py.

        :param enable: Whether to enable subprocess debugging.
        :type enable: bool
        '''

        if enable:
            try:
                import debugpy
                if not debugpy.is_client_connected():
                    raise RuntimeError
            except Exception as e:
                raise NotImplementedError('debugging subprocesses requires debugpy running on current process') from e

        self._subprocess_debugging = enable


    def make_sandbox_setting(self, templates: str = 'default', settings: typing.Optional[list[str]] = None):
        '''
        A utility function to create settings for a sandbox.

        :param templates: Comma-separated prescribed templates. Supported values are 'default' and 'piped_execution'.
                          'default' gives a reasonable set of default configuration, and 'piped_execution' allows
                          process execution with pipe support (see `piped_execute()`).
        :type templates: str, optional
        :param settings: User-provided settings, appended to the templates if any.
        :type settings: typing.Optional[list[str]], optional
        '''

        ret = []
        if templates:
            for template in templates.split(','):
                ret.extend(Sandboxie.SETTING_TEMPLATES[template])
        if settings:
            ret.extend(settings)
        return ret

    def create_sandbox(self, name: str, settings: typing.Optional[list[str]] = None, exist_ok: bool = False):
        '''
        Creates a sandbox. Requires admin right to modify the sandboxie settings.

        :param name: Name of the sandbox to create.
        :type name: str
        :param settings: Settings for the created sandbox, represented by the list of lines. Use
                         `Sandboxie.make_sandbox_setting()` to create one.
        :type settings: typing.Optional[list[str]], optional
        :param exist_ok: If false and there are already a sandbox with a same name, FileExistsError will occur.
        :type exist_ok: bool, optional
        '''
        cfg = self._readini()

        if name in cfg:
            if not exist_ok:
                raise FileExistsError(f'sandbox "{name}" already exists')
            return

        cfg[name] = settings or []

        self._writeini(cfg)
        self._reloadini()

    def get_sandbox_settings(self, name: str = DEFAULTBOX) -> list[str]:
        '''
        Retrieves the settings for an existing sandbox.

        :param name: Name of the sandbox.
        :type name: str, optional
        :return: List of strings, each of which represents an entry (line).
        :rtype: list[str]
        '''
        cfg = self._readini()
        return cfg[name]

    def set_sandbox_settings(self, name: str = DEFAULTBOX, settings: typing.Optional[list[str]] = None):
        '''
        Overwrites the settings for an existing sandbox. Requires admin right to modify the sandboxie settings.

        If the target sandbox is missing, raises FileNotFoundError.

        :param name: Name of the sandbox.
        :type name: str, optional
        :param settings: New settings for the sandbox, represented by the list of lines. Use
                         `Sandboxie.make_sandbox_setting()` to create one.
        :type settings: typing.Optional[list[str]], optional
        '''
        cfg = self._readini()

        if name not in cfg:
            raise FileNotFoundError(f'sandbox "{name}" not found') # coverage: no cover

        cfg[name] = settings

        self._writeini(cfg)
        self._reloadini()

    def terminate_sandbox_processes(self, name: str = DEFAULTBOX):
        '''
        Terminates all processes in a sandbox.

        If the target sandbox is missing, raises FileNotFoundError.

        :param name: Name of the sandbox.
        :type name: str, optional
        '''
        cfg = self._readini()

        if name not in cfg:
            raise FileNotFoundError(f'sandbox "{name}" not found')

        subprocess.run([self._startpath, f'/box:{name}', '/terminate'], check=True)

    def listpids(self, name: str = DEFAULTBOX) -> list[int]:
        '''
        Returns process ID of currently running processes in a sandbox.

        If the target sandbox is missing, raises FileNotFoundError.

        :param name: Name of the sandbox.
        :type name: str, optional
        '''
        cfg = self._readini()

        if name not in cfg:
            raise FileNotFoundError(f'sandbox "{name}" not found')

        output = subprocess.check_output([self._startpath, f'/box:{name}', '/listpids'])
        return [int(pidstr) for pidstr in output.split()[1:]] # 0th is a length parameter

    def delete_content(self, name: str = DEFAULTBOX):
        '''
        Deletes the content of a sandbox.

        If the target sandbox is missing, raises FileNotFoundError.

        :param name: Name of the sandbox.
        :type name: str, optional
        '''
        cfg = self._readini()

        if name not in cfg:
            raise FileNotFoundError(f'sandbox "{name}" not found')

        self.terminate_sandbox_processes(name=name)

        subprocess.run([self._startpath, f'/box:{name}', 'delete_sandbox_silent'], check=True)

    def remove_sandbox(self, name: str, preserve_content: bool = False):
        '''
        Removes a sandbox.

        If the target sandbox is missing, raises FileNotFoundError.

        :param name: Name of the sandbox.
        :type name: str
        :param preserve_content: If true, the content will be intact so that a recreated sandbox with same name may
                                 access those data.
        :type preserve_content: bool, optional
        '''

        cfg = self._readini()

        if name not in cfg:
            raise FileNotFoundError(f'sandbox "{name}" not found')

        self.terminate_sandbox_processes(name)
        if not preserve_content:
            self.delete_content(name)

        del cfg[name]

        self._writeini(cfg)
        self._reloadini()

    def execute(
        self, cmd: list[str], name: str = DEFAULTBOX, uac: bool = False, hide_window: bool = False
    ) -> subprocess.Popen:
        '''
        Executes a process in a sandbox, and returns a `subprocess.Popen` handle to the launcher (Start.exe).

        Note: The returned handle is NOT a handle to the actual process, but merely to the launcher. You may `wait()`
              and retrieve the `returncode` attribute for inspecting the exit code of the sandboxed process since
              the launcher process properly propagates them to its own. However, you have no access to the standard
              handles (stdin/stdout/stderr) since a sandboxed process share no console with the host process. Also, 
              sending signals such as `signal.CTRL_C_EVENT` won't reach the sandboxed process.

              For capturing std-handles as pipe, see `piped_execute()`.

        :param cmd: List of arguments.
        :type cmd: list[str]
        :param name: Name of the sandbox.
        :type name: str, optional
        :param uac: True if you wish to run the process in UAC elevation mode. If the current process does not have UAC
                    right, the UAC confirmation will appear.
        :type uac: bool, optional
        :param hide_window: True if you wish to hide the window.
        :type hide_window: bool, optional
        :return: the `subprocess.Popen` object pointing to the launcher (Start.exe).
        :rtype: subprocess.Popen
        '''

        invocation_sandboxie = [self._startpath, f'/box:{name}', '/wait']
        if uac:
            invocation_sandboxie.append('/elevate')
        if hide_window:
            invocation_sandboxie.append('/hide_window')

        if self._subprocess_debugging: # coverage: no cover
            # Here we simulate how debugpy (pydevd, to be exact) tries to hook subprocess for debugging.
            # REF: https://github.com/microsoft/debugpy/blob/2341614e1451fb0482c6ca5288d77d730b259cea/src/debugpy/_vendored/pydevd/_pydev_bundle/pydev_monkey.py#L707

            from debugpy._vendored.pydevd._pydev_bundle.pydev_monkey import patch_args, send_process_created_message
            cmd = patch_args(cmd)
            send_process_created_message()
            
        # This environ modification is relevant when testing.
        # Unfortunately coverage.py DONT work over the sandboxie boundary.
        # So we disable pytest-cov automatically applying coverage.py when the subprocess starts.
        old_ccs = os.environ.get('COV_CORE_SOURCE', None)
        if old_ccs:
            del os.environ['COV_CORE_SOURCE']

        proc = subprocess.Popen(invocation_sandboxie + cmd)

        if old_ccs:
            os.environ['COV_CORE_SOURCE'] = old_ccs
        
        return proc

    def piped_execute(
        self, cmd: list[str], name: str = DEFAULTBOX, uac: bool = False, hide_window: bool = False
    ) -> SandboxiePipedProcess:
        '''
        Executes a process in a sandbox with stdin/stdout/stderr piped, and returns a `SandboxiePipedProcess` handle.

        Note: The target sandbox must have allowed full access to the named pipes prefixed by `Sandboxie._PIPE_PREFIX`.
              Using `make_sandbox_setting()` with `piped_execution` template yields a setting that grants the access.

        :param cmd: List of arguments.
        :type cmd: list[str]
        :param name: Name of the sandbox.
        :type name: str, optional
        :param uac: True if you wish to run the process in UAC elevation mode. If the current process does not have UAC
                    right, the UAC confirmation will appear.
        :type uac: bool, optional
        :param hide_window: True if you wish to hide the window.
        :type hide_window: bool, optional
        :return: the `SandboxiePipedProcess` object.
        :rtype: SandboxiePipedProcess
        '''
        stdin_pipeserver = win32namedpipe.temppipeserver(
            self._PIPE_PREFIX, inbound=False, outbound=True
        )
        stdout_pipeserver = win32namedpipe.temppipeserver(
            self._PIPE_PREFIX, inbound=True, outbound=False
        )
        stderr_pipeserver = win32namedpipe.temppipeserver(
            self._PIPE_PREFIX, inbound=True, outbound=False, buffering=0
        )

        cmd = [
            sys.executable, str(Path(__file__).parent / 'sandbox_stub_redirector.py'),
            f'--stdin={stdin_pipeserver.name}',
            f'--stdout={stdout_pipeserver.name}',
            f'--stderr={stderr_pipeserver.name}'
        ] + [f'-a{arg}' for arg in cmd]

        stdin = stdin_pipeserver.accept(skip_connection_wait=True)
        stdout = stdout_pipeserver.accept(skip_connection_wait=True)
        stderr = stderr_pipeserver.accept(skip_connection_wait=True)

        popen = self.execute(cmd, name=name, uac=uac, hide_window=hide_window)

        stdin_pipeserver.wait_for_connection(stdin)
        stdout_pipeserver.wait_for_connection(stdout)
        stderr_pipeserver.wait_for_connection(stderr)

        return SandboxiePipedProcess(popen, stdin, stdout, stderr)
