# pysandboxie: a python binding to Sandboxie

## Prerequisites

- Python 3.9+
- [Sandboxie](https://github.com/sandboxie-plus/Sandboxie) 5.45.0+ (you don't need the Sandboxie-Plus launcher)

## Basic Usage

```python
import sandboxie

# Initialize Sandboxie object
sbie = sandboxie.Sandboxie()

# Create a sandbox "testpy" with default settings
settings = sbie.make_sandbox_setting('default')
sbie.create_sandbox('testpy', settings=settings)

# Executes notepad inside it
proc = sbie.execute(['notepad.exe'], 'testpy')
# and retrieve its exit code
print(proc.wait())

# Run, terminate processes, delete the contents, and remove the sandbox entirely
sbie.execute(['notepad.exe'], 'testpy')
sbie.terminate_sandbox_processes('testpy')
sbie.delete_content('testpy')   
sbie.remove_sandbox('testpy')
```

## Piped execution of sandboxed processes

The sandboxed processes do not share the console or pipe handles with their parents, so usually you don't have access toward their standard handles to communicate. Fortunately, pysandboxie provides a *piped execution* feature that allows standard handles to be piped:

```python
import sandboxie

sbie = sandboxie.Sandboxie()

# REQUIRED: apply 'piped_execution' template into settings so that we may initiate IPC
settings = sbie.make_sandbox_setting('default,piped_execution')
sbie.create_sandbox('testpy', settings=settings)

# Run cmd and provide input like subprocess.Popen
proc = sbie.piped_execute(['cmd'], name='testpy', hide_window=True)
with proc:
    proc.stdin.write(b'cd\nexit\n')
    proc.stdin.close()
    print(proc.stdout.read().decode())
    
    # Microsoft Windows [Version 10.0.18363.1198]
    # (c) 2019 Microsoft Corporation. All rights reserved.
    # 
    # D:\temp>cd
    # D:\temp
    # 
    # D:\temp>exit

# Check exit code
assert proc.returncode == 0
```

Internally it is implemented by creating named pipes that can be shared with sandboxed child. The stub process redirects the standard IO to these pipe.

## Debugging the sandboxed python processes

If you're debugging with [debugpy](https://github.com/microsoft/debugpy) via VS Code or PTVS, you may debug the sandboxed python processes just like any other processes. By calling `enable_subprocess_debugging(True)` before running the sandboxed processes, the active debugger will try to attach to the sandboxed processes as well.

## Note on UAC elevation

Some methods require UAC admin rights to modify the configuration file (located in `%windir%\Sandboxie.ini`). Also, you may explicitly request UAC elevation for sandboxed processes by setting `uac=True` to `execute()` and `piped_execute()` functions -- this will show UAC elevation confirm screen if the parent process is not in UAC context.

## Alternatives

[sandboxie-py](https://github.com/gg/sandboxie-py) is a predecessor of this project. Unfortunately it hasn't been maintained from 2012 and has a critical bug on manipulating the settings.