import faulthandler
import io
import logging
import os
import pdb
import sys
import warnings
from io import BytesIO

import pytest

import click
from click.exceptions import ClickException
from click.testing import ABSENT
from click.testing import CliRunner
from click.testing import GlobalWatch
from click.testing import LeakError
from click.testing import LeakReport
from click.utils import _get_binary_stream


def test_runner():
    @click.command()
    def test():
        i = _get_binary_stream("stdin")
        o = _get_binary_stream("stdout")
        while True:
            chunk = i.read(4096)
            if not chunk:
                break
            o.write(chunk)
            o.flush()

    runner = CliRunner()
    result = runner.invoke(test, input="Hello World!\n")
    assert not result.exception
    assert result.output == "Hello World!\n"


def test_echo_stdin_stream():
    @click.command()
    def test():
        i = _get_binary_stream("stdin")
        o = _get_binary_stream("stdout")
        while True:
            chunk = i.read(4096)
            if not chunk:
                break
            o.write(chunk)
            o.flush()

    runner = CliRunner(echo_stdin=True)
    result = runner.invoke(test, input="Hello World!\n")
    assert not result.exception
    assert result.output == "Hello World!\nHello World!\n"


def test_echo_stdin_prompts():
    @click.command()
    def test_python_input():
        foo = input("Foo: ")
        click.echo(f"foo={foo}")

    runner = CliRunner(echo_stdin=True)
    result = runner.invoke(test_python_input, input="bar bar\n")
    assert not result.exception
    assert result.output == "Foo: bar bar\nfoo=bar bar\n"

    @click.command()
    @click.option("--foo", prompt=True)
    def test_prompt(foo):
        click.echo(f"foo={foo}")

    result = runner.invoke(test_prompt, input="bar bar\n")
    assert not result.exception
    assert result.output == "Foo: bar bar\nfoo=bar bar\n"

    @click.command()
    @click.option("--foo", prompt=True, hide_input=True)
    def test_hidden_prompt(foo):
        click.echo(f"foo={foo}")

    result = runner.invoke(test_hidden_prompt, input="bar bar\n")
    assert not result.exception
    assert result.output == "Foo: \nfoo=bar bar\n"

    @click.command()
    @click.option("--foo", prompt=True)
    @click.option("--bar", prompt=True)
    def test_multiple_prompts(foo, bar):
        click.echo(f"foo={foo}, bar={bar}")

    result = runner.invoke(test_multiple_prompts, input="one\ntwo\n")
    assert not result.exception
    assert result.output == "Foo: one\nBar: two\nfoo=one, bar=two\n"


def test_runner_with_stream():
    @click.command()
    def test():
        i = _get_binary_stream("stdin")
        o = _get_binary_stream("stdout")
        while True:
            chunk = i.read(4096)
            if not chunk:
                break
            o.write(chunk)
            o.flush()

    runner = CliRunner()
    result = runner.invoke(test, input=BytesIO(b"Hello World!\n"))
    assert not result.exception
    assert result.output == "Hello World!\n"

    runner = CliRunner(echo_stdin=True)
    result = runner.invoke(test, input=BytesIO(b"Hello World!\n"))
    assert not result.exception
    assert result.output == "Hello World!\nHello World!\n"


def test_prompts():
    @click.command()
    @click.option("--foo", prompt=True)
    def test(foo):
        click.echo(f"foo={foo}")

    runner = CliRunner()
    result = runner.invoke(test, input="wau wau\n")
    assert not result.exception
    assert result.output == "Foo: wau wau\nfoo=wau wau\n"

    @click.command()
    @click.option("--foo", prompt=True, hide_input=True)
    def test(foo):
        click.echo(f"foo={foo}")

    runner = CliRunner()
    result = runner.invoke(test, input="wau wau\n")
    assert not result.exception
    assert result.output == "Foo: \nfoo=wau wau\n"


def test_getchar():
    @click.command()
    def continue_it():
        click.echo(click.getchar())

    runner = CliRunner()
    result = runner.invoke(continue_it, input="y")
    assert not result.exception
    assert result.output == "y\n"

    runner = CliRunner(echo_stdin=True)
    result = runner.invoke(continue_it, input="y")
    assert not result.exception
    assert result.output == "y\n"

    @click.command()
    def getchar_echo():
        click.echo(click.getchar(echo=True))

    runner = CliRunner()
    result = runner.invoke(getchar_echo, input="y")
    assert not result.exception
    assert result.output == "yy\n"

    runner = CliRunner(echo_stdin=True)
    result = runner.invoke(getchar_echo, input="y")
    assert not result.exception
    assert result.output == "yy\n"


def test_catch_exceptions():
    class CustomError(Exception):
        pass

    @click.command()
    def cli():
        raise CustomError(1)

    runner = CliRunner()

    result = runner.invoke(cli)
    assert isinstance(result.exception, CustomError)
    assert type(result.exc_info) is tuple
    assert len(result.exc_info) == 3

    with pytest.raises(CustomError):
        runner.invoke(cli, catch_exceptions=False)

    CustomError = SystemExit

    result = runner.invoke(cli)
    assert result.exit_code == 1


def test_catch_exceptions_cli_runner():
    """Test that invoke `catch_exceptions` takes the value from CliRunner if not set
    explicitly."""

    class CustomError(Exception):
        pass

    @click.command()
    def cli():
        raise CustomError(1)

    runner = CliRunner(catch_exceptions=False)

    result = runner.invoke(cli, catch_exceptions=True)
    assert isinstance(result.exception, CustomError)
    assert type(result.exc_info) is tuple
    assert len(result.exc_info) == 3

    with pytest.raises(CustomError):
        runner.invoke(cli)


def test_with_color():
    @click.command()
    def cli():
        click.secho("hello world", fg="blue")

    runner = CliRunner()

    result = runner.invoke(cli)
    assert result.output == "hello world\n"
    assert not result.exception

    result = runner.invoke(cli, color=True)
    assert result.output == f"{click.style('hello world', fg='blue')}\n"
    assert not result.exception


def test_with_color_errors():
    class CLIError(ClickException):
        def format_message(self) -> str:
            return click.style(self.message, fg="red")

    @click.command()
    def cli():
        raise CLIError("Red error")

    runner = CliRunner()

    result = runner.invoke(cli)
    assert result.output == "Error: Red error\n"
    assert result.exception

    result = runner.invoke(cli, color=True)
    assert result.output == f"Error: {click.style('Red error', fg='red')}\n"
    assert result.exception


def test_with_color_but_pause_not_blocking():
    @click.command()
    def cli():
        click.pause()

    runner = CliRunner()
    result = runner.invoke(cli, color=True)
    assert not result.exception
    assert result.output == ""


def test_with_echo_via_pager():
    @click.command()
    def cli():
        click.echo_via_pager("Hello, Click!")

    runner = CliRunner()
    result = runner.invoke(cli)
    assert not result.exception
    assert result.output == "Hello, Click!\n"


def test_exit_code_and_output_from_sys_exit():
    # See issue #362
    @click.command()
    def cli_string():
        click.echo("hello world")
        sys.exit("error")

    @click.command()
    @click.pass_context
    def cli_string_ctx_exit(ctx):
        click.echo("hello world")
        ctx.exit("error")

    @click.command()
    def cli_int():
        click.echo("hello world")
        sys.exit(1)

    @click.command()
    @click.pass_context
    def cli_int_ctx_exit(ctx):
        click.echo("hello world")
        ctx.exit(1)

    @click.command()
    def cli_float():
        click.echo("hello world")
        sys.exit(1.0)

    @click.command()
    @click.pass_context
    def cli_float_ctx_exit(ctx):
        click.echo("hello world")
        ctx.exit(1.0)

    @click.command()
    def cli_no_error():
        click.echo("hello world")

    runner = CliRunner()

    result = runner.invoke(cli_string)
    assert result.exit_code == 1
    assert result.output == "hello world\nerror\n"

    result = runner.invoke(cli_string_ctx_exit)
    assert result.exit_code == 1
    assert result.output == "hello world\nerror\n"

    result = runner.invoke(cli_int)
    assert result.exit_code == 1
    assert result.output == "hello world\n"

    result = runner.invoke(cli_int_ctx_exit)
    assert result.exit_code == 1
    assert result.output == "hello world\n"

    result = runner.invoke(cli_float)
    assert result.exit_code == 1
    assert result.output == "hello world\n1.0\n"

    result = runner.invoke(cli_float_ctx_exit)
    assert result.exit_code == 1
    assert result.output == "hello world\n1.0\n"

    result = runner.invoke(cli_no_error)
    assert result.exit_code == 0
    assert result.output == "hello world\n"


def test_env():
    @click.command()
    def cli_env():
        click.echo(f"ENV={os.environ['TEST_CLICK_ENV']}")

    runner = CliRunner()

    env_orig = dict(os.environ)
    env = dict(env_orig)
    assert "TEST_CLICK_ENV" not in env
    env["TEST_CLICK_ENV"] = "some_value"
    result = runner.invoke(cli_env, env=env)
    assert result.exit_code == 0
    assert result.output == "ENV=some_value\n"

    assert os.environ == env_orig


def test_stderr():
    @click.command()
    def cli_stderr():
        click.echo("1 - stdout")
        click.echo("2 - stderr", err=True)
        click.echo("3 - stdout")
        click.echo("4 - stderr", err=True)

    runner_mix = CliRunner()
    result_mix = runner_mix.invoke(cli_stderr)

    assert result_mix.output == "1 - stdout\n2 - stderr\n3 - stdout\n4 - stderr\n"
    assert result_mix.stdout == "1 - stdout\n3 - stdout\n"
    assert result_mix.stderr == "2 - stderr\n4 - stderr\n"

    @click.command()
    def cli_empty_stderr():
        click.echo("stdout")

    runner = CliRunner()
    result = runner.invoke(cli_empty_stderr)

    assert result.output == "stdout\n"
    assert result.stdout == "stdout\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args, expected_output",
    [
        (None, "bar\n"),
        ([], "bar\n"),
        ("", "bar\n"),
        (["--foo", "one two"], "one two\n"),
        ('--foo "one two"', "one two\n"),
    ],
)
def test_args(args, expected_output):
    @click.command()
    @click.option("--foo", default="bar")
    def cli_args(foo):
        click.echo(foo)

    runner = CliRunner()
    result = runner.invoke(cli_args, args=args)
    assert result.exit_code == 0
    assert result.output == expected_output


def test_setting_prog_name_in_extra():
    @click.command()
    def cli():
        click.echo("ok")

    runner = CliRunner()
    result = runner.invoke(cli, prog_name="foobar")
    assert not result.exception
    assert result.output == "ok\n"


def test_command_standalone_mode_returns_value():
    @click.command()
    def cli():
        click.echo("ok")
        return "Hello, World!"

    runner = CliRunner()
    result = runner.invoke(cli, standalone_mode=False)
    assert result.output == "ok\n"
    assert result.return_value == "Hello, World!"
    assert result.exit_code == 0


def test_file_stdin_attrs(runner):
    @click.command()
    @click.argument("f", type=click.File())
    def cli(f):
        click.echo(f.name)
        click.echo(f.mode, nl=False)

    result = runner.invoke(cli, ["-"])
    assert result.output == "<stdin>\nr"


@pytest.mark.filterwarnings("ignore:'isolated_filesystem':DeprecationWarning")
def test_isolated_runner(runner):
    with runner.isolated_filesystem() as d:
        assert os.path.exists(d)

    assert not os.path.exists(d)


@pytest.mark.filterwarnings("ignore:'isolated_filesystem':DeprecationWarning")
def test_isolated_runner_custom_tempdir(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as d:
        assert os.path.exists(d)

    assert os.path.exists(d)
    os.rmdir(d)


def test_tempdir_replacement_pattern(runner, tmp_path):
    """The replacement for ``isolated_filesystem()``: a temp dir and absolute paths.

    Rather than changing the working directory, create a directory (here with
    pytest's ``tmp_path``; ``tempfile.TemporaryDirectory`` works the same) and
    pass absolute paths to the command.
    """

    @click.command()
    @click.argument("path", type=click.Path())
    def write_marker(path):
        with open(path, "w") as f:
            f.write("written")

    target = tmp_path / "marker.txt"
    result = runner.invoke(write_marker, [str(target)])

    assert result.exit_code == 0
    assert target.read_text() == "written"


def test_tempdir_isolation_is_thread_safe():
    """The recommended replacement is thread-safe, unlike ``isolated_filesystem()``.

    Each thread owns a ``tempfile.TemporaryDirectory`` and works through
    absolute paths, so there is no ``os.chdir`` and no process-global CWD race
    (the reason ``isolated_filesystem()`` is not thread-safe, issue #3501).

    ``invoke()`` is deliberately not called concurrently here: it redirects the
    process-global standard streams, so parallel invocations in one process
    corrupt each other regardless of the filesystem. To run commands in
    parallel, use separate processes (like pytest-xdist), not threads.
    """
    import tempfile
    import threading

    workers = 8
    cwd_before = os.path.realpath(os.getcwd())
    results: list[tuple[str, str, str]] = []
    barrier = threading.Barrier(workers)
    lock = threading.Lock()

    def worker(index):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "data.txt")
            # Release every thread at once to maximize contention.
            barrier.wait(timeout=10)
            with open(target, "w") as f:
                f.write(f"payload-{index}")
            with open(target) as f:
                content = f.read()
            with lock:
                results.append(
                    (
                        os.path.realpath(d),
                        os.path.realpath(os.getcwd()),
                        content,
                    )
                )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == workers
    # Every thread had its own distinct directory.
    assert len({d for d, _, _ in results}) == workers
    # No thread mutated the process-global working directory.
    assert {cwd for _, cwd, _ in results} == {cwd_before}
    # Each thread read back exactly the payload it wrote, with no cross-talk.
    assert sorted(content for _, _, content in results) == sorted(
        f"payload-{i}" for i in range(workers)
    )


def test_isolation_stderr_errors():
    """Writing to stderr should escape invalid characters instead of
    raising a UnicodeEncodeError.
    """
    runner = CliRunner()

    with runner.isolation() as (_, err, _):
        click.echo("\udce2", err=True, nl=False)
        assert err.getvalue() == b"\\udce2"


def test_isolation_flushes_unflushed_stderr():
    """An un-flushed write to stderr, as with `print(..., file=sys.stderr)`, will end up
    flushed by the runner at end of invocation.
    """
    runner = CliRunner()

    with runner.isolation() as (_, err, _):
        click.echo("\udce2", err=True, nl=False)
        assert err.getvalue() == b"\\udce2"

    @click.command()
    def cli():
        # set end="", flush=False so that it's totally clear that we won't get any
        # auto-flush behaviors
        print("gyarados gyarados gyarados", file=sys.stderr, end="", flush=False)

    result = runner.invoke(cli)
    assert result.stderr == "gyarados gyarados gyarados"


def test_pdb_uses_real_streams():
    """``pdb.Pdb()`` inside ``CliRunner`` defaults to real terminal streams
    so that interactive debuggers work instead of reading from the
    captured ``BytesIO`` stdin.
    """

    @click.command()
    def cli():
        debugger = pdb.Pdb()
        assert debugger.stdin is sys.__stdin__
        assert debugger.stdout is sys.__stdout__
        click.echo("after debugger")

    runner = CliRunner()
    result = runner.invoke(cli, catch_exceptions=False)
    assert result.output == "after debugger\n"


def test_pdb_explicit_streams_honored():
    """Explicit ``stdin``/``stdout`` arguments to ``pdb.Pdb()`` are not
    overridden by the ``CliRunner`` patch.
    """

    @click.command()
    def cli():
        custom_in = sys.stdin
        custom_out = sys.stdout
        debugger = pdb.Pdb(stdin=custom_in, stdout=custom_out)
        assert debugger.stdin is custom_in
        assert debugger.stdout is custom_out

    runner = CliRunner()
    runner.invoke(cli, catch_exceptions=False)


def test_pdb_init_restored_after_invoke():
    """``pdb.Pdb.__init__`` is restored to its original after invoke."""
    original = pdb.Pdb.__init__

    @click.command()
    def cli():
        pass

    runner = CliRunner()
    runner.invoke(cli)

    assert pdb.Pdb.__init__ is original


needs_fd_capture = pytest.mark.skipif(
    sys.platform == "win32",
    reason="fd capture not supported on Windows",
)


def test_capture_invalid_mode():
    """Invalid capture mode raises ValueError."""
    with pytest.raises(ValueError, match="capture"):
        CliRunner(capture="invalid")  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
def test_capture_fd_windows_error():
    """fd capture raises ValueError on Windows."""
    with pytest.raises(ValueError, match="not supported on Windows"):
        CliRunner(capture="fd")


@needs_fd_capture
def test_capture_fd_os_write():
    """capture='fd' captures writes to fd 1/2 that bypass sys.stdout."""

    @click.command()
    def cli():
        click.echo("python stdout")
        os.write(1, b"fd stdout\n")
        os.write(2, b"fd stderr\n")

    runner = CliRunner(capture="fd")
    result = runner.invoke(cli)
    assert "python stdout" in result.stdout
    assert "fd stdout" in result.stdout
    assert "fd stderr" in result.stderr


@needs_fd_capture
def test_capture_fd_stale_reference():
    """capture='fd' captures writes from stale stdout references (issue #2874).

    Simulates ``from sys import stdout`` at import time, which grabs
    the real stdout connected to fd 1.  The stale object's underlying
    FileIO uses fd 1, so redirecting fd 1 captures its writes.
    """
    # open(1, ..., closefd=False) creates a writer whose underlying
    # FileIO uses fd 1 directly. This mirrors the real scenario:
    # the original sys.stdout is a TextIOWrapper -> BufferedWriter ->
    # FileIO(fd=1).
    stale_stdout = open(1, "w", closefd=False)  # noqa: SIM115

    @click.command()
    def cli():
        stale_stdout.write("stale write\n")
        stale_stdout.flush()
        click.echo("normal write")

    runner = CliRunner(capture="fd")
    result = runner.invoke(cli)
    assert "normal write" in result.stdout
    assert "stale write" in result.stdout


@needs_fd_capture
def test_capture_fd_logging_handler(tmp_path):
    """capture='fd' captures logging output from a handler holding a stale
    stderr reference (issue #2827).

    stdlib logging.StreamHandler grabs sys.stderr at configuration time.
    Under normal CliRunner (sys-level capture), the handler still writes
    to the original stream object and output is lost. fd-level capture
    redirects the underlying file descriptor, so the writes are captured.
    """
    import logging

    # Create a writer backed by the real fd 2, simulating a handler
    # configured at import time before pytest or CliRunner replaced
    # sys.stderr. open(2, closefd=False) mirrors the real scenario:
    # the original sys.stderr is a TextIOWrapper -> BufferedWriter ->
    # FileIO(fd=2).
    stale_stderr = open(2, "w", closefd=False)  # noqa: SIM115
    handler = logging.StreamHandler(stale_stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger(f"click_test_{tmp_path.name}")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @click.command()
    def cli():
        logger.info("log from stale handler")
        click.echo("normal echo")

    # sys-level capture misses the log line (it bypasses sys.stderr).
    runner_sys = CliRunner(capture="sys")
    result_sys = runner_sys.invoke(cli)
    assert "normal echo" in result_sys.output
    assert "log from stale handler" not in result_sys.output

    # fd-level capture catches it by redirecting fd 2.
    runner_fd = CliRunner(capture="fd")
    result_fd = runner_fd.invoke(cli)
    assert "normal echo" in result_fd.output
    assert "log from stale handler" in result_fd.output

    logger.removeHandler(handler)


@needs_fd_capture
def test_capture_fd_faulthandler():
    """faulthandler.enable() works with capture='fd' (issue #2865)."""

    @click.command()
    def cli():
        faulthandler.enable()
        click.echo("after faulthandler")

    runner = CliRunner(capture="fd")
    result = runner.invoke(cli)
    assert result.exit_code == 0
    assert "after faulthandler" in result.output


def test_capture_sys_fileno_raises():
    """capture='sys' leaves fileno() raising UnsupportedOperation, so user
    code that does ``os.dup2(w, sys.stdout.fileno())`` cannot mutate the host
    runner's stdout (issue #3384).
    """

    @click.command()
    def cli():
        with pytest.raises(io.UnsupportedOperation):
            sys.stdout.fileno()
        with pytest.raises(io.UnsupportedOperation):
            sys.stderr.fileno()
        click.echo("ok")

    runner = CliRunner(capture="sys")
    result = runner.invoke(cli)
    assert result.exit_code == 0, result.output
    assert "ok" in result.stdout


@needs_fd_capture
def test_capture_fd_stderr_separation():
    """capture='fd' properly separates fd-level stdout and stderr."""

    @click.command()
    def cli():
        click.echo("py-out")
        click.echo("py-err", err=True)
        os.write(1, b"fd-out\n")
        os.write(2, b"fd-err\n")

    runner = CliRunner(capture="fd")
    result = runner.invoke(cli)
    assert "py-out" in result.stdout
    assert "fd-out" in result.stdout
    assert "py-err" in result.stderr
    assert "fd-err" in result.stderr
    # Mixed output has all of them
    assert "py-out" in result.output
    assert "py-err" in result.output
    assert "fd-out" in result.output
    assert "fd-err" in result.output


@needs_fd_capture
def test_capture_fd_nesting():
    """Nested CliRunner.invoke() with fd capture works correctly."""

    @click.command("inner")
    def inner_cli():
        click.echo("inner")

    @click.command("outer")
    def outer_cli():
        click.echo("outer")
        os.write(1, b"outer fd\n")
        inner_runner = CliRunner(capture="fd")
        inner_result = inner_runner.invoke(inner_cli)
        click.echo(f"inner captured: {inner_result.stdout.strip()}")

    runner = CliRunner(capture="fd")
    result = runner.invoke(outer_cli)
    assert "outer" in result.stdout
    assert "outer fd" in result.stdout
    assert "inner captured: inner" in result.stdout


@needs_fd_capture
@pytest.mark.parametrize("runner_capture", ["sys", "fd"])
@pytest.mark.parametrize("pytest_fixture", ["capsys", "capfd"])
def test_capture_pytest_matrix(
    request: pytest.FixtureRequest,
    runner_capture: str,
    pytest_fixture: str,
) -> None:
    """Matrix of ``CliRunner`` capture modes and Pytest capture fixtures."""
    pytest_cap = request.getfixturevalue(pytest_fixture)

    @click.command()
    def cli() -> None:
        click.echo("inside-stdout")
        click.echo("inside-stderr", err=True)

    runner = CliRunner(capture=runner_capture)
    result = runner.invoke(cli)

    # CliRunner sees Click's output.
    assert result.exit_code == 0
    assert "inside-stdout" in result.stdout
    assert "inside-stderr" in result.stderr

    # Pytest capture machinery is still working after invoke().
    print("post-invoke-stdout")
    print("post-invoke-stderr", file=sys.stderr)
    captured = pytest_cap.readouterr()
    assert "post-invoke-stdout" in captured.out
    assert "post-invoke-stderr" in captured.err

    # Click output is not leaking into Pytest.
    assert "inside-stdout" not in captured.out
    assert "inside-stderr" not in captured.err


# ---------------------------------------------------------------------------
# Process state leak detection
# ---------------------------------------------------------------------------


@click.command()
def _leak_clean_cmd():
    click.echo("ok")


def test_leak_detection_disabled_by_default():
    """Without detect_leaks there is no report and no behavior change."""
    runner = CliRunner()
    assert runner.detect_leaks is False

    result = runner.invoke(_leak_clean_cmd)

    assert result.exit_code == 0
    assert result.output == "ok\n"
    assert result.leak_report is None


def test_leak_detection_clean_invocation():
    """A command that changes nothing produces an empty report."""
    result = CliRunner(detect_leaks=True).invoke(_leak_clean_cmd)

    report = result.leak_report
    assert isinstance(report, LeakReport)
    assert not report
    assert len(report) == 0
    assert list(report) == []
    assert report.added == []
    assert report.removed == []
    assert report.changed == []
    assert "No process state leaks" in str(report)
    assert result.exit_code == 0
    assert result.output == "ok\n"


def test_leak_report_environment_added_changed_removed():
    runner = CliRunner(detect_leaks=True)
    os.environ["CLICK_LEAK_EXISTING"] = "before"
    os.environ["CLICK_LEAK_REMOVED"] = "gone"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_NEW"] = "new"
        os.environ["CLICK_LEAK_EXISTING"] = "after"
        del os.environ["CLICK_LEAK_REMOVED"]

    try:
        result = runner.invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_EXISTING", None)
        os.environ.pop("CLICK_LEAK_NEW", None)
        os.environ.pop("CLICK_LEAK_REMOVED", None)

    by_name = {item.name: item for item in result.leak_report}

    added = by_name["os.environ['CLICK_LEAK_NEW']"]
    assert added.change == "added"
    assert added.added
    assert added.before is ABSENT
    assert added.after == "new"

    changed = by_name["os.environ['CLICK_LEAK_EXISTING']"]
    assert changed.change == "changed"
    assert changed.changed
    assert changed.before == "before"
    assert changed.after == "after"

    removed = by_name["os.environ['CLICK_LEAK_REMOVED']"]
    assert removed.change == "removed"
    assert removed.removed
    assert removed.before == "gone"
    assert removed.after is ABSENT


def test_leak_report_environment_self_restore_is_clean():
    """A command that puts every variable back does not leak."""
    os.environ["CLICK_LEAK_SELF"] = "x"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_SELF"] = "y"
        os.environ["CLICK_LEAK_TEMP"] = "z"
        os.environ["CLICK_LEAK_SELF"] = "x"
        del os.environ["CLICK_LEAK_TEMP"]

    try:
        result = CliRunner(detect_leaks=True).invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_SELF", None)

    assert not result.leak_report


def test_leak_report_working_directory(tmp_path):
    cwd = os.getcwd()
    runner = CliRunner(detect_leaks=True)

    @click.command()
    def cli():
        os.chdir(tmp_path)

    result = runner.invoke(cli)
    os.chdir(cwd)

    items = [item for item in result.leak_report if item.name == "os.getcwd()"]
    assert len(items) == 1
    assert items[0].change == "changed"
    assert items[0].before == cwd
    assert os.path.realpath(items[0].after) == os.path.realpath(tmp_path)


def test_leak_working_directory_restored_by_command(tmp_path):
    """Chdir plus chdir-back inside the command is not a leak."""

    @click.command()
    def cli():
        cwd = os.getcwd()
        os.chdir(tmp_path)
        os.chdir(cwd)

    result = CliRunner(detect_leaks=True).invoke(cli)

    assert not [item for item in result.leak_report if item.name == "os.getcwd()"]


def test_leak_report_warnings_filters():
    marker = "click_leak_warning_marker_unique"
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    def cli():
        warnings.filterwarnings("ignore", message=f"{marker}.*")

    result = runner.invoke(cli)

    items = [item for item in result.leak_report if item.name == "warnings.filters"]
    assert len(items) == 1
    assert items[0].change == "changed"
    assert marker in repr(items[0].after)
    assert marker not in repr(warnings.filters)


def test_leak_report_logging():
    logger_name = "click_leak_unique_logger"
    root_level_before = logging.getLogger().level
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    def cli():
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(logging.StreamHandler())
        logging.getLogger().setLevel(logging.ERROR)

    result = runner.invoke(cli)

    names = [item.name for item in result.leak_report]
    assert f"logging.getLogger({logger_name!r})" in names
    assert "logging.root.level" in names
    # The restore cleaned both the named logger and the root configuration.
    assert logger_name not in logging.Logger.manager.loggerDict
    assert logging.getLogger().level == root_level_before


def test_leak_report_sys_modules():
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    def cli():
        import colorsys  # noqa: F401

    result = runner.invoke(cli)

    items = [
        item for item in result.leak_report if item.name == "sys.modules['colorsys']"
    ]
    assert len(items) == 1
    assert items[0].change == "added"
    assert items[0].before is ABSENT
    assert items[0].after.__name__ == "colorsys"
    assert "colorsys" not in sys.modules


def test_leak_report_click_context_stack():
    pushed: dict[str, click.Context] = {}
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    @click.pass_context
    def cli(ctx):
        # Push without popping: a leftover context on the thread-local stack.
        click.globals.push_context(ctx)
        pushed["ctx"] = ctx

    result = runner.invoke(cli)

    items = [
        item for item in result.leak_report if item.name == "click.globals._local.stack"
    ]
    assert len(items) == 1
    assert pushed["ctx"] in items[0].after
    # The restore popped the leftover context.
    assert click.globals.get_current_context(silent=True) is None


def test_leak_ignores_runner_env_overrides():
    """Keys isolation overrides and restores are not reported."""
    runner = CliRunner(detect_leaks=True, env={"CLICK_LEAK_OVERRIDE": "x"})

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_OVERRIDE"] = "mutated"
        os.environ["CLICK_LEAK_OTHER"] = "z"

    try:
        result = runner.invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_OTHER", None)

    names = [item.name for item in result.leak_report]
    assert "os.environ['CLICK_LEAK_OVERRIDE']" not in names
    assert "os.environ['CLICK_LEAK_OTHER']" in names
    assert "CLICK_LEAK_OVERRIDE" not in os.environ


def test_leak_watch_env_tracks_overridden_key():
    """watch_env opts into a key the runner itself overrides."""
    runner = CliRunner(
        detect_leaks=True,
        env={"CLICK_LEAK_WATCHED": "entry"},
        watch_env=["CLICK_LEAK_WATCHED"],
    )

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_WATCHED"] = "mutated"

    result = runner.invoke(cli)

    items = [item for item in result.leak_report if "CLICK_LEAK_WATCHED" in item.name]
    assert len(items) == 1
    assert items[0].before == "entry"
    assert items[0].after == "mutated"
    assert "CLICK_LEAK_WATCHED" not in os.environ


def test_leak_restore_environment():
    """restore_leaks puts variables back but keeps the report."""
    runner = CliRunner(detect_leaks=True, restore_leaks=True)
    os.environ["CLICK_LEAK_RESTORE"] = "before"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_RESTORE"] = "after"
        os.environ["CLICK_LEAK_RESTORE_NEW"] = "new"

    try:
        result = runner.invoke(cli)
        # The report keeps the evidence, the variables are restored.
        assert result.leak_report
        assert os.environ["CLICK_LEAK_RESTORE"] == "before"
        assert "CLICK_LEAK_RESTORE_NEW" not in os.environ
    finally:
        os.environ.pop("CLICK_LEAK_RESTORE", None)
        os.environ.pop("CLICK_LEAK_RESTORE_NEW", None)


def test_leak_restore_working_directory(tmp_path):
    cwd = os.getcwd()
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    def cli():
        os.chdir(tmp_path)

    result = runner.invoke(cli)

    assert result.leak_report
    assert os.path.realpath(os.getcwd()) == os.path.realpath(cwd)


def test_leak_watch_globals_custom_and_restore():
    import types

    module = types.ModuleType("click_leak_custom_globals")
    module.VALUE = 0
    module.ITEMS = [1]
    runner = CliRunner(
        detect_leaks=True,
        restore_leaks=True,
        watch_globals={
            "custom.VALUE": GlobalWatch(module, "VALUE"),
            "custom.ITEMS": GlobalWatch(module, "ITEMS", copier=list),
        },
    )

    @click.command()
    def cli():
        module.VALUE = 1
        module.ITEMS.append(2)

    result = runner.invoke(cli)

    by_name = {item.name: item for item in result.leak_report}
    assert (by_name["custom.VALUE"].before, by_name["custom.VALUE"].after) == (
        0,
        1,
    )
    assert (by_name["custom.ITEMS"].before, by_name["custom.ITEMS"].after) == (
        [1],
        [1, 2],
    )
    # Restored to the entry values.
    assert module.VALUE == 0
    assert module.ITEMS == [1]


def test_leak_watch_lists_are_appendable():
    import types

    module = types.ModuleType("click_leak_append_globals")
    module.FLAG = False
    runner = CliRunner(detect_leaks=True)
    runner.watch_env.add("CLICK_LEAK_APPEND_ENV")
    runner.watch_globals["custom.FLAG"] = GlobalWatch(module, "FLAG")

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_APPEND_ENV"] = "x"
        module.FLAG = True

    try:
        result = runner.invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_APPEND_ENV", None)

    names = {item.name for item in result.leak_report}
    assert "os.environ['CLICK_LEAK_APPEND_ENV']" in names
    assert "custom.FLAG" in names


def test_leak_raise_on_leak():
    runner = CliRunner(detect_leaks=True, raise_on_leak=True)

    # A clean invocation still returns normally.
    assert runner.invoke(_leak_clean_cmd).exit_code == 0

    os.environ["CLICK_LEAK_RAISE"] = "x"

    @click.command()
    def cli():
        del os.environ["CLICK_LEAK_RAISE"]

    try:
        with pytest.raises(LeakError) as exc_info:
            runner.invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_RAISE", None)

    report = exc_info.value.report
    assert isinstance(report, LeakReport)
    assert any(
        item.name == "os.environ['CLICK_LEAK_RAISE']" and item.removed
        for item in report
    )
    assert "CLICK_LEAK_RAISE" in str(exc_info.value)


def test_leak_invoke_level_overrides():
    @click.command()
    def cli():
        os.environ["CLICK_LEAK_INVOKE"] = "x"

    try:
        # Enabled per invoke on a default runner.
        result = CliRunner().invoke(cli, detect_leaks=True)
        assert result.leak_report

        # The first invoke left its leak in the process (report-only mode);
        # clean up so the next invocation starts from the same baseline.
        os.environ.pop("CLICK_LEAK_INVOKE", None)

        # raise_on_leak disabled per invoke on a raising runner.
        runner = CliRunner(detect_leaks=True, raise_on_leak=True)
        result = runner.invoke(cli, raise_on_leak=False)
        assert result.leak_report
        os.environ.pop("CLICK_LEAK_INVOKE", None)

        # Detection disabled per invoke on a detecting runner.
        runner = CliRunner(detect_leaks=True)
        result = runner.invoke(cli, detect_leaks=False)
        assert result.leak_report is None
    finally:
        os.environ.pop("CLICK_LEAK_INVOKE", None)


def test_leak_each_invoke_uses_own_entry_snapshot():
    os.environ["CLICK_LEAK_COUNT"] = "0"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_COUNT"] = str(int(os.environ["CLICK_LEAK_COUNT"]) + 1)

    runner = CliRunner(detect_leaks=True)

    try:
        r1 = runner.invoke(cli)
        r2 = runner.invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_COUNT", None)

    i1 = next(
        item for item in r1.leak_report if item.name == "os.environ['CLICK_LEAK_COUNT']"
    )
    i2 = next(
        item for item in r2.leak_report if item.name == "os.environ['CLICK_LEAK_COUNT']"
    )
    assert (i1.before, i1.after) == ("0", "1")
    assert (i2.before, i2.after) == ("1", "2")


@pytest.mark.filterwarnings("ignore:'isolated_filesystem':DeprecationWarning")
def test_leak_with_isolated_filesystem(tmp_path):
    """Cwd is compared against the invoke entry cwd; isolation keeps its
    own temp-dir semantics."""
    cwd = os.getcwd()
    runner = CliRunner(detect_leaks=True)

    @click.command()
    def stay():
        pass

    @click.command()
    def leave():
        os.chdir(tmp_path)

    with runner.isolated_filesystem(temp_dir=tmp_path) as temp_dir:
        result = runner.invoke(stay)
        assert not result.leak_report

        result = runner.invoke(leave)
        cwd_items = [item for item in result.leak_report if item.name == "os.getcwd()"]
        assert len(cwd_items) == 1
        assert os.path.realpath(cwd_items[0].before) == os.path.realpath(temp_dir)
        assert os.path.realpath(cwd_items[0].after) == os.path.realpath(tmp_path)
        # Detection reports but does not itself move the directory.
        assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path)

    # isolated_filesystem still restores the host directory as always.
    assert os.path.realpath(os.getcwd()) == os.path.realpath(cwd)


def test_leak_detection_does_not_change_execution():
    @click.command()
    def cli():
        click.echo(os.environ.get("CLICK_LEAK_VIEW", "default"))

    plain = CliRunner().invoke(cli)
    detected = CliRunner(detect_leaks=True).invoke(cli)
    assert plain.output == detected.output == "default\n"

    plain = CliRunner(env={"CLICK_LEAK_VIEW": "x"}).invoke(cli)
    detected = CliRunner(detect_leaks=True, env={"CLICK_LEAK_VIEW": "x"}).invoke(cli)
    assert plain.output == detected.output == "x\n"


def test_leak_report_is_readable():
    os.environ["CLICK_LEAK_READABLE_EXISTING"] = "before"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_READABLE_NEW"] = "after"
        del os.environ["CLICK_LEAK_READABLE_EXISTING"]

    try:
        result = CliRunner(detect_leaks=True).invoke(cli)
    finally:
        os.environ.pop("CLICK_LEAK_READABLE_NEW", None)
        os.environ.pop("CLICK_LEAK_READABLE_EXISTING", None)

    text = str(result.leak_report)
    assert "added:" in text
    assert "removed:" in text
    assert "CLICK_LEAK_READABLE_NEW" in text
    assert "CLICK_LEAK_READABLE_EXISTING" in text
    assert "<absent>" in text
    assert "'after'" in text
    assert repr(result.leak_report).startswith("<LeakReport")


def test_leak_restore_with_uncaught_exception(tmp_path):
    """catch_exceptions=False: restoration still runs, original exception
    is not masked and no report is produced."""
    cwd = os.getcwd()
    runner = CliRunner(detect_leaks=True, restore_leaks=True)

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_BOOM"] = "x"
        os.chdir(tmp_path)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        runner.invoke(cli, catch_exceptions=False)

    assert "CLICK_LEAK_BOOM" not in os.environ
    assert os.path.realpath(os.getcwd()) == os.path.realpath(cwd)


def test_leak_nested_invokes_are_independent():
    """A detecting invoke nested inside another command reports the inner
    leak but restores it, so the outer invocation sees a clean process."""

    @click.command()
    def inner():
        os.environ["CLICK_LEAK_NESTED"] = "x"

    @click.command()
    def outer():
        inner_result = CliRunner(detect_leaks=True, restore_leaks=True).invoke(inner)
        # The inner command leaked; the report keeps that evidence even
        # though the runner restored the variable afterwards.
        click.echo(str(bool(inner_result.leak_report)))
        click.echo(str("CLICK_LEAK_NESTED" in os.environ))

    result = CliRunner(detect_leaks=True).invoke(outer)

    assert result.output == "True\nFalse\n"
    assert not result.leak_report
    assert "CLICK_LEAK_NESTED" not in os.environ


def test_leak_raise_and_restore_together():
    """State is restored before LeakError is raised."""
    runner = CliRunner(detect_leaks=True, raise_on_leak=True, restore_leaks=True)
    os.environ["CLICK_LEAK_RAISE_RESTORE"] = "before"

    @click.command()
    def cli():
        os.environ["CLICK_LEAK_RAISE_RESTORE"] = "after"

    try:
        with pytest.raises(LeakError):
            runner.invoke(cli)
        # Restored even though the error was raised.
        assert os.environ["CLICK_LEAK_RAISE_RESTORE"] == "before"
    finally:
        os.environ.pop("CLICK_LEAK_RAISE_RESTORE", None)
