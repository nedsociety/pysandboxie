import sys
import subprocess
import win32namedpipe
import clize


def main(
    *, args: (str, 'a', clize.parameters.multi(min=1)), stdin: str = None, stdout: str = None, stderr: str = None
):
    '''
    A stub for launching applications in Sandboxie.

    :param args: An argument to launch app. This option can be specified multiple times.
    :type args: list[str]
    :param stdin: The path for stdin redirection.
    :type stdin: str, optional
    :param stdout: The path for stdout redirection.
    :type stdout: str, optional
    :param stderr: The path for stderr redirection.
    :type stderr: str, optional
    '''

    if stdin is not None:
        stdin = win32namedpipe.Win32NamedPipeClient(stdin, inbound=True, outbound=False).connect()

    if stdout is not None:
        stdout = win32namedpipe.Win32NamedPipeClient(stdout, inbound=False, outbound=True).connect()

    if stderr is not None:
        stderr = win32namedpipe.Win32NamedPipeClient(stderr, inbound=False, outbound=True, buffering=0).connect()

    with stdin:
        with stdout:
            with stderr:
                proc = subprocess.run(args, stdin=stdin, stdout=stdout, stderr=stderr)
                sys.exit(proc.returncode)


if __name__ == '__main__':
    clize.run(main, exit=False)
