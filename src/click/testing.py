from __future__ import annotations

import collections.abc as cabc
import contextlib
import importlib
import io
import logging
import os
import pdb
import shlex
import sys
import tempfile
import typing as t
import warnings
from types import TracebackType

from . import _compat
from . import formatting
from . import globals as click_globals
from . import termui
from . import utils
from ._compat import _find_binary_reader

if t.TYPE_CHECKING:
    from _typeshed import ReadableBuffer

    from .core import Command

if sys.platform == "win32":
    CaptureMode: t.TypeAlias = t.Literal["sys"]  # pyright: ignore[reportRedeclaration]
else:
    CaptureMode: t.TypeAlias = t.Literal["sys", "fd"]  # pyright: ignore[reportRedeclaration]
ExceptionInfo: t.TypeAlias = tuple[type[BaseException], BaseException, TracebackType]


class EchoingStdin:
    _input: t.BinaryIO
    _output: t.BinaryIO
    _paused: bool

    def __init__(self, input: t.BinaryIO, output: t.BinaryIO) -> None:
        self._input = input
        self._output = output
        self._paused = False

    def __getattr__(self, x: str) -> t.Any:
        return getattr(self._input, x)

    def _echo(self, rv: bytes) -> bytes:
        if not self._paused:
            self._output.write(rv)

        return rv

    def read(self, n: int = -1) -> bytes:
        return self._echo(self._input.read(n))

    def read1(self, n: int = -1) -> bytes:
        return self._echo(self._input.read1(n))  # type: ignore

    def readline(self, n: int = -1) -> bytes:
        return self._echo(self._input.readline(n))

    def readlines(self) -> list[bytes]:
        return [self._echo(x) for x in self._input.readlines()]

    def __iter__(self) -> cabc.Iterator[bytes]:
        return iter(self._echo(x) for x in self._input)

    def __repr__(self) -> str:
        return repr(self._input)


@contextlib.contextmanager
def _pause_echo(stream: EchoingStdin | None) -> cabc.Generator[None]:
    if stream is None:
        yield
    else:
        stream._paused = True
        yield
        stream._paused = False


class _FDCapture:
    """Redirect a file descriptor to a temporary file for capture.

    Saves the current target of *targetfd* via :func:`os.dup`, then
    redirects it to a temporary file via :func:`os.dup2`. On
    :meth:`stop`, restores the original ``fd`` and returns the captured
    bytes. Inspired by Pytest's ``FDCapture``.

    .. versionadded:: 8.4.0
    """

    _targetfd: int
    saved_fd: int
    _tmpfile: t.BinaryIO | None

    def __init__(self, targetfd: int) -> None:
        self._targetfd = targetfd
        self.saved_fd = -1
        self._tmpfile = None

    def start(self) -> None:
        self.saved_fd = os.dup(self._targetfd)
        self._tmpfile = tempfile.TemporaryFile(buffering=0)
        os.dup2(self._tmpfile.fileno(), self._targetfd)

    def stop(self) -> bytes:
        assert self._tmpfile is not None, "_FDCapture.start() was not called"
        os.dup2(self.saved_fd, self._targetfd)
        os.close(self.saved_fd)
        self.saved_fd = -1
        self._tmpfile.seek(0)
        data = self._tmpfile.read()
        self._tmpfile.close()
        self._tmpfile = None
        return data


class BytesIOCopy(io.BytesIO):
    """Patch ``io.BytesIO`` to let the written stream be copied to another.

    .. versionadded:: 8.2
    """

    copy_to: io.BytesIO

    def __init__(self, copy_to: io.BytesIO) -> None:
        super().__init__()
        self.copy_to = copy_to

    def flush(self) -> None:
        super().flush()
        self.copy_to.flush()

    def write(self, b: ReadableBuffer) -> int:
        self.copy_to.write(b)
        return super().write(b)


class StreamMixer:
    """Mixes `<stdout>` and `<stderr>` streams.

    The result is available in the ``output`` attribute.

    .. versionadded:: 8.2
    """

    output: io.BytesIO
    stdout: BytesIOCopy
    stderr: BytesIOCopy

    def __init__(self) -> None:
        self.output = io.BytesIO()
        self.stdout = BytesIOCopy(copy_to=self.output)
        self.stderr = BytesIOCopy(copy_to=self.output)


class _NamedTextIOWrapper(io.TextIOWrapper):
    """A :class:`~io.TextIOWrapper` with custom ``name`` and ``mode``
    that does not close its underlying buffer.

    When ``CliRunner`` runs in ``fd`` mode, ``_original_fd`` is patched to
    point at the saved (pre-redirection) ``fd``, so C-level consumers that call
    :meth:`fileno` (like ``faulthandler`` or ``subprocess``) keep working. In
    the default ``sys`` mode ``_original_fd`` stays at ``-1`` and
    :meth:`fileno` raises :exc:`io.UnsupportedOperation`, matching the
    pre-``8.3.3`` behavior.
    """

    _name: str
    _mode: str
    _original_fd: int

    def __init__(
        self,
        buffer: t.BinaryIO,
        name: str,
        mode: str,
        **kwargs: t.Any,
    ) -> None:
        super().__init__(buffer, **kwargs)
        self._name = name
        self._mode = mode
        self._original_fd = -1

    def close(self) -> None:
        """The buffer this object contains belongs to some other object,
        so prevent the default ``__del__`` implementation from closing
        that buffer.

        .. versionadded:: 8.3.2
        """

    def fileno(self) -> int:
        """Return the file descriptor of the saved original stream when
        ``CliRunner`` runs in ``fd`` mode. Otherwise delegate to
        :class:`~io.TextIOWrapper`, which raises
        :exc:`io.UnsupportedOperation` for a ``BytesIO``-backed buffer.
        """
        if self._original_fd >= 0:
            return self._original_fd
        return super().fileno()

    @property
    def name(self) -> str:
        return self._name

    @property
    def mode(self) -> str:
        return self._mode


def make_input_stream(
    input: str | bytes | t.IO[t.Any] | None, charset: str
) -> t.BinaryIO:
    # Is already an input stream.
    if hasattr(input, "read"):
        rv = _find_binary_reader(t.cast("t.IO[t.Any]", input))

        if rv is not None:
            return rv

        raise TypeError("Could not find binary reader for input stream.")

    if input is None:
        input = b""
    elif isinstance(input, str):
        input = input.encode(charset)

    return io.BytesIO(input)


# ---------------------------------------------------------------------------
# Process state leak detection
# ---------------------------------------------------------------------------

LeakChange: t.TypeAlias = t.Literal["added", "removed", "changed"]


class _Absent:
    """Singleton standing in for a value that did not exist on one side of
    a leak comparison, such as a deleted environment variable.
    """

    _instance: _Absent | None = None

    def __new__(cls) -> _Absent:
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __repr__(self) -> str:
        return "<absent>"

    def __bool__(self) -> bool:
        return False


#: Placeholder for a value missing before (``added``) or after (``removed``)
#: an invocation.
ABSENT: t.Final[_Absent] = _Absent()

#: Modules Click may import lazily while running a command. They are imported
#: before the snapshot so the runner itself does not show up as a
#: ``sys.modules`` leak.
_PREWARM_MODULES: tuple[str, ...] = (
    "copy",
    "difflib",
    "errno",
    "getpass",
    "glob",
    "importlib.metadata",
    "keyword",
    "locale",
    "logging",
    "operator",
    "random",
    "shutil",
    "subprocess",
    "tempfile",
    "textwrap",
    "warnings",
    "webbrowser",
    "click.shell_completion",
)


def _prewarm_modules() -> None:
    """Import the modules Click may load lazily during an invocation.

    This keeps the runner's own lazy imports out of ``sys.modules`` leak
    reports. Platform-specific modules are imported on a best-effort basis.
    """
    names = [*_PREWARM_MODULES, "msvcrt" if sys.platform == "win32" else "termios"]

    if sys.platform != "win32":
        names.append("tty")

    for name in names:
        try:
            importlib.import_module(name)
        except Exception:
            pass


def _format_value(value: t.Any, max_length: int = 200) -> str:
    """Render a snapshot value for human-readable leak reports."""
    if value is ABSENT:
        return repr(ABSENT)

    try:
        rendered = repr(value)
    except Exception as e:
        rendered = f"<unreprable {type(value).__name__}: {e}>"

    if len(rendered) > max_length:
        rendered = f"{rendered[: max_length - 16]}...<truncated>"

    return rendered


class LeakItem:
    """A single process-state change left behind after an invocation.

    :param name: What changed, for example ``"os.environ['FOO']"``.
    :param before: The value before the invocation. :data:`ABSENT` means the
        name did not exist yet.
    :param after: The value after the invocation. :data:`ABSENT` means it was
        removed.
    :param change: One of ``"added"``, ``"removed"`` or ``"changed"``.

    .. versionadded:: 8.6
    """

    name: str
    before: t.Any
    after: t.Any
    change: LeakChange

    def __init__(
        self, name: str, before: t.Any, after: t.Any, change: LeakChange
    ) -> None:
        self.name = name
        self.before = before
        self.after = after
        self.change = change

    @property
    def added(self) -> bool:
        """Whether this item did not exist before the invocation."""
        return self.change == "added"

    @property
    def removed(self) -> bool:
        """Whether this item no longer exists after the invocation."""
        return self.change == "removed"

    @property
    def changed(self) -> bool:
        """Whether this item existed on both sides with different values."""
        return self.change == "changed"

    def __str__(self) -> str:
        return (
            f"{self.change:>7}: {self.name}:"
            f" {_format_value(self.before)} -> {_format_value(self.after)}"
        )

    def __repr__(self) -> str:
        return f"<LeakItem {self.change} {self.name}>"


class LeakReport:
    """The process-state differences left after a :meth:`CliRunner.invoke`.

    Iterating yields :class:`LeakItem` instances. A report is falsy when no
    leaks were detected.

    .. versionadded:: 8.6
    """

    items: tuple[LeakItem, ...]

    def __init__(self, items: cabc.Iterable[LeakItem] = ()) -> None:
        self.items = tuple(items)

    @property
    def added(self) -> list[LeakItem]:
        """Names created during the invocation and not removed again."""
        return [item for item in self.items if item.added]

    @property
    def removed(self) -> list[LeakItem]:
        """Names deleted during the invocation."""
        return [item for item in self.items if item.removed]

    @property
    def changed(self) -> list[LeakItem]:
        """Names whose values differ before and after the invocation."""
        return [item for item in self.items if item.changed]

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> cabc.Iterator[LeakItem]:
        return iter(self.items)

    def __getitem__(self, index: int | slice) -> t.Any:
        return self.items[index]

    def __str__(self) -> str:
        if not self.items:
            return "No process state leaks detected."

        lines = [
            f"Process state leaked during CliRunner.invoke ({len(self.items)} item(s)):"
        ]
        lines.extend(f"  {item}" for item in self.items)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<LeakReport ({len(self.items)} item(s))>"


class LeakError(AssertionError):
    """Raised when leak detection is enabled with ``raise_on_leak`` and a
    :class:`LeakReport` is not empty.

    The full report is available as :attr:`report` and in the exception
    message.

    .. versionadded:: 8.6
    """

    report: LeakReport

    def __init__(self, report: LeakReport) -> None:
        self.report = report
        super().__init__(str(report))


class GlobalWatch:
    """Watch one attribute on a global object for process-state leaks.

    :param obj: The object or module owning the attribute, for example an
        imported configuration module.
    :param attr: The attribute name to snapshot before and after an
        invocation.
    :param copier: Optional callable returning an independent copy of the
        value. Required to detect in-place mutation of mutable values such as
        lists or dicts (for example ``list`` or ``dict.copy``). When not
        given, plain equality is used, which detects reassignment but not
        in-place changes.

    Register watches through the ``watch_globals`` mapping of
    :class:`CliRunner` or by appending to :attr:`CliRunner.watch_globals`.

    .. versionadded:: 8.6
    """

    obj: t.Any
    attr: str
    copier: t.Callable[[t.Any], t.Any] | None

    def __init__(
        self,
        obj: t.Any,
        attr: str,
        copier: t.Callable[[t.Any], t.Any] | None = None,
    ) -> None:
        self.obj = obj
        self.attr = attr
        self.copier = copier


class _Watchpoint:
    """One named piece of process state that can leak between invocations."""

    name: str

    def snapshot(self) -> t.Any:
        raise NotImplementedError

    def snapshot_inside(self) -> t.Any:
        """Snapshot taken after isolation applied its own overrides.

        Only the environment watchpoint uses this: it lets explicitly
        watched keys be tracked even when the runner itself overrides them.
        """
        return None

    def diff(self, before: t.Any, after: t.Any) -> list[LeakItem]:
        try:
            equal = before == after
        except Exception:
            equal = before is after

        if equal:
            return []

        return [LeakItem(self.name, before, after, "changed")]

    def diff_inside(self, inside_before: t.Any, inside_after: t.Any) -> list[LeakItem]:
        """Compare snapshots taken inside isolation.

        Only the environment watchpoint uses this, for explicitly watched
        keys that the runner itself overrides.
        """
        return []

    def restore(self, before: t.Any) -> None:
        """Best-effort restoration of the entry snapshot. Only called when
        the runner is configured to restore leaks."""


class _EnvironWatchpoint(_Watchpoint):
    """Compare every process environment variable.

    Keys the runner overrides through its own isolation are ignored by
    default, since isolation restores them itself. Explicitly registered
    ``extra_keys`` are additionally tracked inside isolation, so a command
    mutating a value the runner overwrote is still reported.
    """

    name = "os.environ"

    def __init__(
        self,
        ignored_keys: cabc.Collection[str] = (),
        extra_keys: cabc.Collection[str] = (),
    ) -> None:
        self.ignored_keys = set(ignored_keys)
        self.extra_keys = set(extra_keys)

    @property
    def _tracked_overrides(self) -> set[str]:
        """Overridden keys the caller explicitly asked to watch."""
        return self.ignored_keys & self.extra_keys

    def snapshot(self) -> dict[str, str]:
        return dict(os.environ)

    def snapshot_inside(self) -> dict[str, str | _Absent]:
        return {
            key: os.environ[key] if key in os.environ else ABSENT
            for key in self._tracked_overrides
        }

    def diff(self, before: dict[str, str], after: dict[str, str]) -> list[LeakItem]:
        items: list[LeakItem] = []

        # Everything the command leaves in the process environment, except
        # keys isolation owns and restores itself.
        for key in sorted((before.keys() | after.keys()) - self.ignored_keys):
            label = f"os.environ[{key!r}]"
            existed_before = key in before
            exists_after = key in after

            if existed_before and exists_after:
                if before[key] != after[key]:
                    items.append(LeakItem(label, before[key], after[key], "changed"))
            elif exists_after:
                items.append(LeakItem(label, ABSENT, after[key], "added"))
            else:
                items.append(LeakItem(label, before[key], ABSENT, "removed"))

        return items

    def diff_inside(
        self,
        inside_before: dict[str, str | _Absent],
        inside_after: dict[str, str | _Absent],
    ) -> list[LeakItem]:
        # Explicitly watched keys that the runner overrides through
        # isolation: compare the values the command actually saw.
        items: list[LeakItem] = []

        for key in sorted(self._tracked_overrides):
            old = inside_before.get(key, ABSENT)
            new = inside_after.get(key, ABSENT)

            if old != new:
                items.append(LeakItem(f"os.environ[{key!r}]", old, new, "changed"))

        return items

    def restore(self, before: dict[str, str]) -> None:
        for key, value in before.items():
            if os.environ.get(key) != value:
                os.environ[key] = value

        for key in list(os.environ):
            if key not in before:
                try:
                    del os.environ[key]
                except OSError:
                    pass


class _CwdWatchpoint(_Watchpoint):
    name = "os.getcwd()"

    def snapshot(self) -> str | _Absent:
        try:
            return os.getcwd()
        except OSError:
            # The current directory may have been removed under us.
            return ABSENT

    def restore(self, before: str | _Absent) -> None:
        if isinstance(before, str) and self.snapshot() != before:
            try:
                os.chdir(before)
            except OSError:
                pass


class _WarningsWatchpoint(_Watchpoint):
    """Watch the active :data:`warnings.filters` list."""

    name = "warnings.filters"

    def snapshot(self) -> list[t.Any]:
        return list(warnings.filters)

    def restore(self, before: list[t.Any]) -> None:
        # Replace in place: other code may hold a reference to the list.
        t.cast("list[t.Any]", warnings.filters)[:] = before


class _SysModulesWatchpoint(_Watchpoint):
    """Watch modules added to or removed from :data:`sys.modules`."""

    name = "sys.modules"

    def snapshot(self) -> dict[str, t.Any]:
        return dict(sys.modules)

    def diff(self, before: dict[str, t.Any], after: dict[str, t.Any]) -> list[LeakItem]:
        items: list[LeakItem] = []

        for key in sorted(before.keys() | after.keys()):
            label = f"sys.modules[{key!r}]"

            if key not in before:
                items.append(LeakItem(label, ABSENT, after[key], "added"))
            elif key not in after:
                items.append(LeakItem(label, before[key], ABSENT, "removed"))

        return items

    def restore(self, before: dict[str, t.Any]) -> None:
        for key in list(sys.modules):
            if key not in before:
                sys.modules.pop(key, None)

        for key, module in before.items():
            sys.modules.setdefault(key, module)


class _LoggingWatchpoint(_Watchpoint):
    """Watch the root logger configuration and named loggers."""

    name = "logging"

    @staticmethod
    def _logger_state(logger: logging.Logger) -> tuple[t.Any, ...]:
        return (
            logger.level,
            tuple(id(handler) for handler in logger.handlers),
            logger.disabled,
        )

    def snapshot(self) -> dict[str, t.Any]:
        root = logging.getLogger()
        return {
            "root_level": root.level,
            "root_handlers": list(root.handlers),
            "loggers": dict(logging.Logger.manager.loggerDict),
        }

    def diff(self, before: dict[str, t.Any], after: dict[str, t.Any]) -> list[LeakItem]:
        items: list[LeakItem] = []

        if before["root_level"] != after["root_level"]:
            items.append(
                LeakItem(
                    "logging.root.level",
                    before["root_level"],
                    after["root_level"],
                    "changed",
                )
            )

        before_handlers = before["root_handlers"]
        after_handlers = after["root_handlers"]

        if [id(h) for h in before_handlers] != [id(h) for h in after_handlers]:
            items.append(
                LeakItem(
                    "logging.root.handlers",
                    before_handlers,
                    after_handlers,
                    "changed",
                )
            )

        before_loggers = before["loggers"]
        after_loggers = after["loggers"]

        for name in sorted(before_loggers.keys() | after_loggers.keys()):
            label = f"logging.getLogger({name!r})"
            old = before_loggers.get(name, ABSENT)
            new = after_loggers.get(name, ABSENT)

            if old is ABSENT and new is not ABSENT:
                items.append(LeakItem(label, ABSENT, new, "added"))
            elif new is ABSENT and old is not ABSENT:
                items.append(LeakItem(label, old, ABSENT, "removed"))
            elif isinstance(old, logging.Logger) and isinstance(new, logging.Logger):
                if self._logger_state(old) != self._logger_state(new):
                    items.append(LeakItem(label, old, new, "changed"))
            elif old is not new:
                # Placeholder <-> Logger transition.
                items.append(LeakItem(label, old, new, "changed"))

        return items

    def restore(self, before: dict[str, t.Any]) -> None:
        root = logging.getLogger()
        root.setLevel(before["root_level"])
        root.handlers[:] = before["root_handlers"]

        manager = logging.Logger.manager
        old_loggers = before["loggers"]

        for name in list(manager.loggerDict):
            if name not in old_loggers:
                manager.loggerDict.pop(name, None)

        for name, logger in old_loggers.items():
            if manager.loggerDict.get(name) is not logger:
                manager.loggerDict[name] = logger


class _ClickContextStackWatchpoint(_Watchpoint):
    """Watch Click's thread-local context stack for leftover contexts."""

    name = "click.globals._local.stack"

    @staticmethod
    def _get_stack() -> list[t.Any] | None:
        return click_globals._local.__dict__.get("stack")

    def snapshot(self) -> list[t.Any]:
        stack = self._get_stack()
        return list(stack) if stack is not None else []

    def diff(self, before: list[t.Any], after: list[t.Any]) -> list[LeakItem]:
        if [id(ctx) for ctx in before] != [id(ctx) for ctx in after]:
            return [LeakItem(self.name, before, after, "changed")]

        return []

    def restore(self, before: list[t.Any]) -> None:
        stack = self._get_stack()

        if stack is not None:
            while len(stack) > len(before):
                stack.pop()


class _GlobalWatchAdapter(_Watchpoint):
    """Adapt a user-registered :class:`GlobalWatch` to a watchpoint."""

    def __init__(self, name: str, watch: GlobalWatch) -> None:
        self.name = name
        self.watch = watch

    def snapshot(self) -> t.Any:
        value = getattr(self.watch.obj, self.watch.attr)

        if self.watch.copier is not None:
            value = self.watch.copier(value)

        return value

    def restore(self, before: t.Any) -> None:
        setattr(self.watch.obj, self.watch.attr, before)


class Result:
    """Holds the captured result of an invoked CLI script.

    :param runner: The runner that created the result
    :param stdout_bytes: The standard output as bytes.
    :param stderr_bytes: The standard error as bytes.
    :param output_bytes: A mix of ``stdout_bytes`` and ``stderr_bytes``, as the
        user would see  it in its terminal.
    :param return_value: The value returned from the invoked command.
    :param exit_code: The exit code as integer.
    :param exception: The exception that happened if one did.
    :param exc_info: Exception information (exception type, exception instance,
        traceback type).
    :param leak_report: Process-state leaks detected during the invocation.
        ``None`` when leak detection was not enabled, otherwise an empty
        :class:`LeakReport` when nothing leaked.

    .. versionadded:: 8.6
        Added ``leak_report``.

    .. versionchanged:: 8.2
        ``stderr_bytes`` no longer optional, ``output_bytes`` introduced and
        ``mix_stderr`` has been removed.

    .. versionadded:: 8.0
        Added ``return_value``.
    """

    runner: CliRunner
    stdout_bytes: bytes
    stderr_bytes: bytes
    output_bytes: bytes
    return_value: t.Any
    exit_code: int
    exception: BaseException | None
    exc_info: ExceptionInfo | None
    leak_report: LeakReport | None

    def __init__(
        self,
        runner: CliRunner,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        output_bytes: bytes,
        return_value: t.Any,
        exit_code: int,
        exception: BaseException | None,
        exc_info: ExceptionInfo | None = None,
        leak_report: LeakReport | None = None,
    ) -> None:
        self.runner = runner
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes
        self.output_bytes = output_bytes
        self.return_value = return_value
        self.exit_code = exit_code
        self.exception = exception
        self.exc_info = exc_info
        self.leak_report = leak_report

    @property
    def output(self) -> str:
        """The terminal output as unicode string, as the user would see it.

        .. versionchanged:: 8.2
            No longer a proxy for ``self.stdout``. Now has its own independent stream
            that is mixing `<stdout>` and `<stderr>`, in the order they were written.
        """
        return self.output_bytes.decode(self.runner.charset, "replace").replace(
            "\r\n", "\n"
        )

    @property
    def stdout(self) -> str:
        """The standard output as unicode string."""
        return self.stdout_bytes.decode(self.runner.charset, "replace").replace(
            "\r\n", "\n"
        )

    @property
    def stderr(self) -> str:
        """The standard error as unicode string.

        .. versionchanged:: 8.2
            No longer raise an exception, always returns the `<stderr>` string.
        """
        return self.stderr_bytes.decode(self.runner.charset, "replace").replace(
            "\r\n", "\n"
        )

    def __repr__(self) -> str:
        exc_str = repr(self.exception) if self.exception else "okay"
        return f"<{type(self).__name__} {exc_str}>"


class CliRunner:
    """The CLI runner provides functionality to invoke a Click command line
    script for unittesting purposes in a isolated environment.  This only
    works in single-threaded systems without any concurrency as it changes the
    global interpreter state.

    :param charset: the character set for the input and output data.
    :param env: a dictionary with environment variables for overriding.
    :param echo_stdin: if this is set to `True`, then reading from `<stdin>` writes
                       to `<stdout>`.  This is useful for showing examples in
                       some circumstances.  Note that regular prompts
                       will automatically echo the input.
    :param catch_exceptions: Whether to catch any exceptions other than
                             ``SystemExit`` when running :meth:`~CliRunner.invoke`.
    :param capture: Selects the output capture strategy. ``sys`` (default)
        captures Python-level writes only and leaves
        :meth:`sys.stdout.fileno` raising :exc:`io.UnsupportedOperation`, so
        user code that calls :func:`os.dup2` on ``sys.stdout.fileno()`` cannot
        clobber the host runner's stdout. ``fd`` redirects file descriptors
        ``1`` and ``2`` via :func:`os.dup2` to a temporary file, also catching
        output from stale stream references, C extensions, and subprocesses.
        ``fd`` is not supported on Windows.
    :param detect_leaks: Snapshot the process environment, working directory
        and a convention list of global state before each invocation and
        report what the invoked command left changed on
        :attr:`Result.leak_report`. Keys overridden through the runner's own
        isolation are not reported. Defaults to off, in which case there is
        no snapshot overhead.
    :param raise_on_leak: When leak detection is enabled, raise
        :class:`LeakError` carrying the full :class:`LeakReport` as soon as a
        non-empty report is produced.
    :param restore_leaks: When leak detection is enabled, restore leaked
        environment variables, the working directory and watched globals to
        their entry values after the report is produced. The report still
        records what leaked.
    :param watch_env: Additional environment variable names to watch. All
        environment variables are compared already; names listed here are
        compared even when the runner itself overrides them through
        isolation.
    :param watch_globals: Additional global objects to watch, mapping a
        report label to a :class:`GlobalWatch`. The defaults cover the
        ``logging`` configuration, :data:`warnings.filters`,
        :data:`sys.modules` and Click's context stack.

    .. versionadded:: 8.6
        Added the ``detect_leaks``, ``raise_on_leak``, ``restore_leaks``,
        ``watch_env`` and ``watch_globals`` parameters.

    .. versionchanged:: 8.4.0
        Added the ``capture`` parameter. The default ``sys`` mode no longer
        exposes the original fd through :meth:`fileno`, reverting the change
        introduced in ``8.3.3`` that broke Pytest's ``fd``-level capture
        teardown. Use ``capture="fd"`` to restore that behavior with proper
        isolation. :issue:`3384`

    .. versionchanged:: 8.2
        Added the ``catch_exceptions`` parameter.

    .. versionchanged:: 8.2
        ``mix_stderr`` parameter has been removed.
    """

    charset: str
    env: cabc.Mapping[str, str | None]
    echo_stdin: bool
    catch_exceptions: bool
    capture: CaptureMode
    detect_leaks: bool
    raise_on_leak: bool
    restore_leaks: bool
    watch_env: set[str]
    watch_globals: dict[str, GlobalWatch]

    def __init__(
        self,
        charset: str = "utf-8",
        env: cabc.Mapping[str, str | None] | None = None,
        echo_stdin: bool = False,
        catch_exceptions: bool = True,
        capture: CaptureMode = "sys",
        *,
        detect_leaks: bool = False,
        raise_on_leak: bool = False,
        restore_leaks: bool = False,
        watch_env: cabc.Collection[str] | None = None,
        watch_globals: cabc.Mapping[str, GlobalWatch] | None = None,
    ) -> None:
        if capture not in {"sys", "fd"}:
            raise ValueError(
                f"capture={capture!r} is not valid. Choose from 'sys' or 'fd'."
            )
        if capture == "fd" and sys.platform == "win32":
            raise ValueError(
                f"capture={capture!r} is not supported on Windows. Use 'sys'."
            )
        self.charset = charset
        self.env = env or {}
        self.echo_stdin = echo_stdin
        self.catch_exceptions = catch_exceptions
        self.capture = capture
        self.detect_leaks = detect_leaks
        self.raise_on_leak = raise_on_leak
        self.restore_leaks = restore_leaks
        self.watch_env = set(watch_env) if watch_env else set()
        self.watch_globals = dict(watch_globals) if watch_globals else {}

    def _make_watchpoints(
        self, env: cabc.Mapping[str, str | None] | None
    ) -> list[_Watchpoint]:
        """Build the watchpoints for one invocation.

        The environment keys the runner overrides through its own isolation
        are excluded: isolation restores them itself.
        """
        ignored = set(self.env)

        if env:
            ignored.update(env)

        points: list[_Watchpoint] = [
            _EnvironWatchpoint(ignored, self.watch_env),
            _CwdWatchpoint(),
            _LoggingWatchpoint(),
            _WarningsWatchpoint(),
            _SysModulesWatchpoint(),
            _ClickContextStackWatchpoint(),
        ]
        points.extend(
            _GlobalWatchAdapter(name, watch)
            for name, watch in self.watch_globals.items()
        )
        return points

    def get_default_prog_name(self, cli: Command) -> str:
        """Given a command object it will return the default program name
        for it.  The default is the `name` attribute or ``"root"`` if not
        set.
        """
        return cli.name or "root"

    def make_env(
        self, overrides: cabc.Mapping[str, str | None] | None = None
    ) -> cabc.Mapping[str, str | None]:
        """Returns the environment overrides for invoking a script."""
        rv = dict(self.env)
        if overrides:
            rv.update(overrides)
        return rv

    @contextlib.contextmanager
    def isolation(
        self,
        input: str | bytes | t.IO[t.Any] | None = None,
        env: cabc.Mapping[str, str | None] | None = None,
        color: bool = False,
    ) -> cabc.Generator[tuple[io.BytesIO, io.BytesIO, io.BytesIO]]:
        """A context manager that sets up the isolation for invoking of a
        command line tool.  This sets up `<stdin>` with the given input data
        and `os.environ` with the overrides from the given dictionary.
        This also rebinds some internals in Click to be mocked (like the
        prompt functionality).

        This is automatically done in the :meth:`invoke` method.

        :param input: the input stream to put into `sys.stdin`.
        :param env: the environment overrides as dictionary.
        :param color: whether the output should contain color codes. The
                      application can still override this explicitly.

        .. versionadded:: 8.2
            An additional output stream is returned, which is a mix of
            `<stdout>` and `<stderr>` streams.

        .. versionchanged:: 8.2
            Always returns the `<stderr>` stream.

        .. versionchanged:: 8.0
            `<stderr>` is opened with ``errors="backslashreplace"``
            instead of the default ``"strict"``.

        .. versionchanged:: 4.0
            Added the ``color`` parameter.
        """
        bytes_input = make_input_stream(input, self.charset)
        echo_input = None

        old_stdin = sys.stdin
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_forced_width = formatting.FORCED_WIDTH
        formatting.FORCED_WIDTH = 80

        env = self.make_env(env)

        stream_mixer = StreamMixer()

        if self.echo_stdin:
            bytes_input = echo_input = t.cast(
                t.BinaryIO, EchoingStdin(bytes_input, stream_mixer.stdout)
            )

        sys.stdin = text_input = _NamedTextIOWrapper(
            bytes_input, encoding=self.charset, name="<stdin>", mode="r"
        )

        if self.echo_stdin:
            # Force unbuffered reads, otherwise TextIOWrapper reads a
            # large chunk which is echoed early.
            text_input._CHUNK_SIZE = 1  # type: ignore

        sys.stdout = _NamedTextIOWrapper(
            stream_mixer.stdout,
            encoding=self.charset,
            name="<stdout>",
            mode="w",
        )

        sys.stderr = _NamedTextIOWrapper(
            stream_mixer.stderr,
            encoding=self.charset,
            name="<stderr>",
            mode="w",
            errors="backslashreplace",
        )

        @_pause_echo(echo_input)  # type: ignore
        def visible_input(prompt: str | None = None) -> str:
            sys.stdout.write(prompt or "")
            try:
                val = next(text_input).rstrip("\r\n")
            except StopIteration as e:
                raise EOFError() from e
            sys.stdout.write(f"{val}\n")
            sys.stdout.flush()
            return val

        @_pause_echo(echo_input)  # type: ignore
        def hidden_input(prompt: str | None = None) -> str:
            sys.stdout.write(f"{prompt or ''}\n")
            sys.stdout.flush()
            try:
                return next(text_input).rstrip("\r\n")
            except StopIteration as e:
                raise EOFError() from e

        @_pause_echo(echo_input)  # type: ignore
        def _getchar(echo: bool) -> str:
            char = sys.stdin.read(1)

            if echo:
                sys.stdout.write(char)

            sys.stdout.flush()
            return char

        default_color = color

        def should_strip_ansi(
            stream: t.IO[t.Any] | None = None, color: bool | None = None
        ) -> bool:
            if color is None:
                return not default_color
            return not color

        old_visible_prompt_func = termui.visible_prompt_func
        old_hidden_prompt_func = termui.hidden_prompt_func
        old__getchar_func = termui._getchar
        old_should_strip_ansi = utils.should_strip_ansi  # type: ignore
        old__compat_should_strip_ansi = _compat.should_strip_ansi
        old_pdb_init = pdb.Pdb.__init__
        termui.visible_prompt_func = visible_input
        termui.hidden_prompt_func = hidden_input
        termui._getchar = _getchar
        utils.should_strip_ansi = should_strip_ansi  # type: ignore
        _compat.should_strip_ansi = should_strip_ansi

        def _patched_pdb_init(
            self: pdb.Pdb,
            completekey: str = "tab",
            stdin: t.IO[str] | None = None,
            stdout: t.IO[str] | None = None,
            **kwargs: t.Any,
        ) -> None:
            """Default ``pdb.Pdb`` to real terminal streams during
            ``CliRunner`` isolation.

            Without this patch, ``pdb.Pdb.__init__`` inherits from
            ``cmd.Cmd`` which falls back to ``sys.stdin``/``sys.stdout``
            when no explicit streams are provided. During isolation
            those are ``BytesIO``-backed wrappers, so the debugger
            reads from an empty buffer and writes to captured output,
            making interactive debugging impossible.

            By defaulting to ``sys.__stdin__``/``sys.__stdout__`` (the
            original terminal streams Python preserves regardless of
            redirection), debuggers can interact with the user while
            ``click.echo`` output is still captured normally.

            This covers ``pdb.set_trace()``, ``breakpoint()``,
            ``pdb.post_mortem()``, and debuggers that subclass
            ``pdb.Pdb`` (ipdb, pdbpp). Explicit ``stdin``/``stdout``
            arguments are honored and not overridden. Debuggers that
            do not subclass ``pdb.Pdb`` (pudb, debugpy) are not
            covered.
            """
            if stdin is None:
                stdin = sys.__stdin__
            if stdout is None:
                stdout = sys.__stdout__
            old_pdb_init(
                self, completekey=completekey, stdin=stdin, stdout=stdout, **kwargs
            )

        pdb.Pdb.__init__ = _patched_pdb_init  # type: ignore[assignment]

        old_env = {}
        try:
            for key, value in env.items():
                old_env[key] = os.environ.get(key)
                if value is None:
                    try:
                        del os.environ[key]
                    except Exception:
                        pass
                else:
                    os.environ[key] = value
            yield (stream_mixer.stdout, stream_mixer.stderr, stream_mixer.output)
        finally:
            for key, value in old_env.items():
                if value is None:
                    try:
                        del os.environ[key]
                    except Exception:
                        pass
                else:
                    os.environ[key] = value
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.stdin = old_stdin
            termui.visible_prompt_func = old_visible_prompt_func
            termui.hidden_prompt_func = old_hidden_prompt_func
            termui._getchar = old__getchar_func
            utils.should_strip_ansi = old_should_strip_ansi  # type: ignore
            _compat.should_strip_ansi = old__compat_should_strip_ansi
            formatting.FORCED_WIDTH = old_forced_width
            pdb.Pdb.__init__ = old_pdb_init  # type: ignore[method-assign]

    def invoke(
        self,
        cli: Command,
        args: str | cabc.Sequence[str] | None = None,
        input: str | bytes | t.IO[t.Any] | None = None,
        env: cabc.Mapping[str, str | None] | None = None,
        catch_exceptions: bool | None = None,
        color: bool = False,
        detect_leaks: bool | None = None,
        raise_on_leak: bool | None = None,
        restore_leaks: bool | None = None,
        **extra: t.Any,
    ) -> Result:
        """Invokes a command in an isolated environment.  The arguments are
        forwarded directly to the command line script, the `extra` keyword
        arguments are passed to the :meth:`~clickpkg.Command.main` function of
        the command.

        This returns a :class:`Result` object.

        :param cli: the command to invoke
        :param args: the arguments to invoke. It may be given as an iterable
                     or a string. When given as string it will be interpreted
                     as a Unix shell command. More details at
                     :func:`shlex.split`.
        :param input: the input data for `sys.stdin`.
        :param env: the environment overrides.
        :param catch_exceptions: Whether to catch any other exceptions than
                                 ``SystemExit``. If :data:`None`, the value
                                 from :class:`CliRunner` is used.
        :param color: whether the output should contain color codes. The
                      application can still override this explicitly.
        :param detect_leaks: Enable process-state leak detection for this
                             invocation. If :data:`None`, the value from
                             :class:`CliRunner` is used.
        :param raise_on_leak: Override the runner's ``raise_on_leak`` setting
                              for this invocation.
        :param restore_leaks: Override the runner's ``restore_leaks`` setting
                              for this invocation.

        .. versionadded:: 8.6
            Added the ``detect_leaks``, ``raise_on_leak`` and
            ``restore_leaks`` parameters and the
            :attr:`Result.leak_report` attribute.

        .. versionadded:: 8.2
            The result object has the ``output_bytes`` attribute with
            the mix of ``stdout_bytes`` and ``stderr_bytes``, as the user would
            see it in its terminal.

        .. versionchanged:: 8.2
            The result object always returns the ``stderr_bytes`` stream.

        .. versionchanged:: 8.0
            The result object has the ``return_value`` attribute with
            the value returned from the invoked command.

        .. versionchanged:: 4.0
            Added the ``color`` parameter.

        .. versionchanged:: 3.0
            Added the ``catch_exceptions`` parameter.

        .. versionchanged:: 3.0
            The result object has the ``exc_info`` attribute with the
            traceback if available.
        """
        exc_info = None
        if catch_exceptions is None:
            catch_exceptions = self.catch_exceptions

        if detect_leaks is None:
            detect_leaks = self.detect_leaks

        if raise_on_leak is None:
            raise_on_leak = self.raise_on_leak

        if restore_leaks is None:
            restore_leaks = self.restore_leaks

        # Snapshot the process state at the entry of this invocation, before
        # isolation replaces streams or environment variables. Each invoke
        # compares against its own entry snapshot.
        watchpoints: list[_Watchpoint] = []
        before_state: list[t.Any] = []

        if detect_leaks:
            _prewarm_modules()
            watchpoints = self._make_watchpoints(env)
            before_state = [wp.snapshot() for wp in watchpoints]
            inside_before_state: list[t.Any] = [None] * len(watchpoints)
            inside_after_state: list[t.Any] = [None] * len(watchpoints)

        # Set up fd capture before isolation replaces sys.stdout and sys.stderr.
        cap_out: _FDCapture | None = None
        cap_err: _FDCapture | None = None

        if self.capture == "fd":
            cap_out = _FDCapture(1)
            cap_err = _FDCapture(2)
            try:
                cap_out.start()
                cap_err.start()
            except OSError:
                cap_out = cap_err = None

        try:
            with self.isolation(input=input, env=env, color=color) as outstreams:
                # Snapshot explicitly watched keys after isolation applied its
                # own environment overrides.
                if detect_leaks:
                    inside_before_state = [wp.snapshot_inside() for wp in watchpoints]

                # Point the captured streams' fileno() at the saved (original)
                # fd so that C-level consumers like faulthandler keep working
                # while fd 1/2 are redirected to the capture tmpfile.
                if cap_out is not None and cap_err is not None:
                    sys.stdout._original_fd = cap_out.saved_fd  # type: ignore[union-attr]
                    sys.stderr._original_fd = cap_err.saved_fd  # type: ignore[union-attr]

                return_value = None
                exception: BaseException | None = None
                exit_code = 0

                if isinstance(args, str):
                    args = shlex.split(args)

                try:
                    prog_name = extra.pop("prog_name")
                except KeyError:
                    prog_name = self.get_default_prog_name(cli)

                try:
                    return_value = cli.main(
                        args=args or (), prog_name=prog_name, **extra
                    )
                except SystemExit as e:
                    exc_info = sys.exc_info()
                    e_code = t.cast("int | t.Any | None", e.code)

                    if e_code is None:
                        e_code = 0

                    if e_code != 0:
                        exception = e

                    if not isinstance(e_code, int):
                        sys.stdout.write(str(e_code))
                        sys.stdout.write("\n")
                        e_code = 1

                    exit_code = e_code

                except Exception as e:
                    if not catch_exceptions:
                        raise
                    exception = e
                    exit_code = 1
                    exc_info = sys.exc_info()
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()

                    # Capture the inside-isolation values of explicitly
                    # watched keys before isolation tears its overrides down.
                    if detect_leaks:
                        inside_after_state = [
                            wp.snapshot_inside() for wp in watchpoints
                        ]

                    # Stop fd capture and merge the captured bytes into
                    # the stdout/stderr BytesIO streams. BytesIOCopy mirrors
                    # those writes into outstreams[2] automatically.
                    if cap_out is not None and cap_err is not None:
                        fd_out = cap_out.stop()
                        fd_err = cap_err.stop()
                        if fd_out:
                            outstreams[0].write(fd_out)
                        if fd_err:
                            outstreams[1].write(fd_err)

                    stdout = outstreams[0].getvalue()
                    stderr = outstreams[1].getvalue()
                    output = outstreams[2].getvalue()

            result = Result(
                runner=self,
                stdout_bytes=stdout,
                stderr_bytes=stderr,
                output_bytes=output,
                return_value=return_value,
                exit_code=exit_code,
                exception=exception,
                exc_info=exc_info,  # type: ignore
            )
        except BaseException:
            # The command escaped (usually catch_exceptions=False). Restore
            # process state when configured, but never mask the original
            # exception; there is no report to attach in that case.
            if detect_leaks and restore_leaks:
                self._restore_state(watchpoints, before_state)

            raise

        if detect_leaks:
            result.leak_report = self._diff_state(
                watchpoints,
                before_state,
                inside_before_state,
                inside_after_state,
            )

            if restore_leaks:
                self._restore_state(watchpoints, before_state)

            if raise_on_leak and result.leak_report:
                raise LeakError(result.leak_report)

        return result

    @staticmethod
    def _diff_state(
        watchpoints: cabc.Sequence[_Watchpoint],
        before_state: cabc.Sequence[t.Any],
        inside_before: cabc.Sequence[t.Any] | None = None,
        inside_after: cabc.Sequence[t.Any] | None = None,
    ) -> LeakReport:
        """Compare fresh snapshots against entry snapshots and build a report."""
        items: list[LeakItem] = []
        inside_before = inside_before or []
        inside_after = inside_after or []

        for index, (watchpoint, before) in enumerate(
            zip(watchpoints, before_state, strict=True)
        ):
            try:
                after = watchpoint.snapshot()
            except Exception:
                continue

            try:
                items.extend(watchpoint.diff(before, after))
            except Exception:
                continue

            if index < len(inside_before) and index < len(inside_after):
                try:
                    items.extend(
                        watchpoint.diff_inside(
                            inside_before[index], inside_after[index]
                        )
                    )
                except Exception:
                    continue

        return LeakReport(items)

    @staticmethod
    def _restore_state(
        watchpoints: cabc.Sequence[_Watchpoint], before_state: cabc.Sequence[t.Any]
    ) -> None:
        """Best-effort restoration of every watchpoint's entry value."""
        for watchpoint, before in zip(watchpoints, before_state, strict=True):
            try:
                watchpoint.restore(before)
            except Exception:
                pass

    @contextlib.contextmanager
    def isolated_filesystem(
        self, temp_dir: str | os.PathLike[str] | None = None
    ) -> cabc.Generator[str]:
        """A context manager that creates a temporary directory and
        changes the current working directory to it. This isolates tests
        that affect the contents of the CWD to prevent them from
        interfering with each other.

        .. warning::
            This helper predates Python 3 and modern pytest, and is not
            thread-safe: it relies on :func:`os.chdir`, which mutates
            process-global state, and :meth:`invoke` swaps the
            process-global standard streams too. Parallelize tests with
            processes (``pytest-xdist``), not threads. Locking the
            runner (:pr:`3511`, :pr:`3520`, :pr:`3530`) and a
            ``set_filesystem()`` API (:issue:`3123`) were declined:
            neither removes the global-state mutation. See :issue:`3700`
            and :issue:`3501`.

        :param temp_dir: Create the temporary directory under this
            directory. If given, the created directory is not removed
            when exiting.

        .. deprecated:: 8.5.0
            Will be removed in Click 9.0. Use
            :class:`tempfile.TemporaryDirectory` or pytest's
            ``tmp_path`` fixture with absolute paths instead.

        .. versionchanged:: 8.0
            Added the ``temp_dir`` parameter.
        """
        import warnings

        warnings.warn(
            "'isolated_filesystem' is deprecated and will be removed in Click"
            " 9.0. Use 'tempfile.TemporaryDirectory' or pytest's 'tmp_path'"
            " fixture with absolute paths instead.",
            DeprecationWarning,
            stacklevel=3,
        )

        cwd = os.getcwd()
        dt = tempfile.mkdtemp(dir=temp_dir)
        os.chdir(dt)

        try:
            yield dt
        finally:
            os.chdir(cwd)

            if temp_dir is None:
                import shutil

                try:
                    shutil.rmtree(dt)
                except OSError:
                    pass
