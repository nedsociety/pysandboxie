import sandboxie
import pytest
import time
import ctypes

# Set this to True when you need to debug the tests.
# Note that this will break the coverage.py so make sure it stays False on version control.
ENABLE_SUBPROCESS_DEBUGGING = False
UAC = ctypes.windll.shell32.IsUserAnAdmin()

@pytest.fixture(scope='module')
def sbie():
    ret = sandboxie.Sandboxie()
    try:
        ret.enable_subprocess_debugging(ENABLE_SUBPROCESS_DEBUGGING)
    except NotImplementedError:
        pass

    if UAC:
        ret.create_sandbox(name='testpy', settings=ret.make_sandbox_setting('default,piped_execution'), exist_ok=True)
    try:
        yield ret
    finally:
        if UAC:
            ret.remove_sandbox(name='testpy')

def test_cmd(sbie):
    sp = sbie.piped_execute(['cmd'], name='testpy', uac=True, hide_window=False)
    with sp:
        sp.stdin.write(b'cd\nexit\n')
        sp.stdin.flush()
        sp.stdin.close()
        print(sp.stdout.read())
    assert sp.returncode == 0

def test_broken_pipe(sbie):
    sp = sbie.piped_execute(['cmd', '/c', 'echo', 'yay'], name='testpy', uac=True, hide_window=True)
    with sp:
        time.sleep(2)
        sp.stdin.write(b'test')
        # the BrokenPipeError would have been silently swallowed by sp.__exit__()
    assert sp.returncode == 0

def test_settings(sbie):
    if not UAC:
        return
    try:
        setting = sbie.make_sandbox_setting('default', ['abc=def'])
        assert len(setting) > 1
        assert setting[-1] == 'abc=def'

        sbie.set_sandbox_settings('testpy', setting)
        assert sbie.get_sandbox_settings('testpy') == setting
    
    finally:
        # Restore to default
        sbie.set_sandbox_settings('testpy', sbie.make_sandbox_setting('default,piped_execution'))

def test_duplicates_and_nonexistents(sbie):
    if not UAC:
        return
    try:
        with pytest.raises(FileExistsError):
            sbie.create_sandbox('testpy', exist_ok=False)
        sbie.remove_sandbox('testpy', preserve_content=True)
        with pytest.raises(FileNotFoundError):
            sbie.remove_sandbox('testpy')
        with pytest.raises(FileNotFoundError):
            sbie.terminate_sandbox_processes('testpy')
        with pytest.raises(FileNotFoundError):
            sbie.delete_content('testpy')
    finally:
        # Restore
        sbie.create_sandbox(name='testpy', settings=sbie.make_sandbox_setting('default,piped_execution'), exist_ok=True)

def test_listpids(sbie):
    sp = sbie.piped_execute(['cmd'], name='testpy', hide_window=False)
    cmdproc_pid = None
    with sp:
        import psutil
        for pid in sbie.listpids(name='testpy'):
            if psutil.Process(pid=pid).name().lower() == 'cmd.exe':
                cmdproc_pid = pid

        sp.stdin.write(b'exit\n')
        sp.stdin.flush()
        sp.stdin.close()
    
    assert cmdproc_pid not in sbie.listpids(name='testpy')
