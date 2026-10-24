import logging
import typing as t
from contextlib import AbstractContextManager
from contextlib import contextmanager
from types import TracebackType

import pytest

import click
from click import Context
from click import Option
from click import Parameter
from click.core import ParameterSource
from click.decorators import help_option
from click.decorators import pass_meta_key


def test_ensure_context_objects(runner):
    class Foo:
        def __init__(self):
            self.title = "default"

    pass_foo = click.make_pass_decorator(Foo, ensure=True)

    @click.group()
    @pass_foo
    def cli(foo):
        pass

    @cli.command()
    @pass_foo
    def test(foo):
        click.echo(foo.title)

    result = runner.invoke(cli, ["test"])
    assert not result.exception
    assert result.output == "default\n"


def test_get_context_objects(runner):
    class Foo:
        def __init__(self):
            self.title = "default"

    pass_foo = click.make_pass_decorator(Foo, ensure=True)

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj = Foo()
        ctx.obj.title = "test"

    @cli.command()
    @pass_foo
    def test(foo):
        click.echo(foo.title)

    result = runner.invoke(cli, ["test"])
    assert not result.exception
    assert result.output == "test\n"


def test_get_context_objects_no_ensuring(runner):
    class Foo:
        def __init__(self):
            self.title = "default"

    pass_foo = click.make_pass_decorator(Foo)

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj = Foo()
        ctx.obj.title = "test"

    @cli.command()
    @pass_foo
    def test(foo):
        click.echo(foo.title)

    result = runner.invoke(cli, ["test"])
    assert not result.exception
    assert result.output == "test\n"


def test_get_context_objects_missing(runner):
    class Foo:
        pass

    pass_foo = click.make_pass_decorator(Foo)

    @click.group()
    @click.pass_context
    def cli(ctx):
        pass

    @cli.command()
    @pass_foo
    def test(foo):
        click.echo(foo.title)

    result = runner.invoke(cli, ["test"])
    assert result.exception is not None
    assert isinstance(result.exception, RuntimeError)
    assert (
        "Managed to invoke callback without a context object of type"
        " 'Foo' existing" in str(result.exception)
    )


def test_multi_enter(runner):
    called = []

    @click.command()
    @click.pass_context
    def cli(ctx):
        def callback():
            called.append(True)

        ctx.call_on_close(callback)

        with ctx:
            pass
        assert not called

    result = runner.invoke(cli, [])
    assert result.exception is None
    assert called == [True]


def test_global_context_object(runner):
    @click.command()
    @click.pass_context
    def cli(ctx):
        assert click.get_current_context() is ctx
        ctx.obj = "FOOBAR"
        assert click.get_current_context().obj == "FOOBAR"

    assert click.get_current_context(silent=True) is None
    runner.invoke(cli, [], catch_exceptions=False)
    assert click.get_current_context(silent=True) is None


def test_context_meta(runner):
    LANG_KEY = f"{__name__}.lang"

    def set_language(value):
        click.get_current_context().meta[LANG_KEY] = value

    def get_language():
        return click.get_current_context().meta.get(LANG_KEY, "en_US")

    @click.command()
    @click.pass_context
    def cli(ctx):
        assert get_language() == "en_US"
        set_language("de_DE")
        assert get_language() == "de_DE"

    runner.invoke(cli, [], catch_exceptions=False)


def test_make_pass_meta_decorator(runner):
    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.meta["value"] = "good"

    @cli.command()
    @pass_meta_key("value")
    def show(value):
        return value

    result = runner.invoke(cli, ["show"], standalone_mode=False)
    assert result.return_value == "good"


def test_make_pass_meta_decorator_doc():
    pass_value = pass_meta_key("value")
    assert "the 'value' key from :attr:`click.Context.meta`" in pass_value.__doc__
    pass_value = pass_meta_key("value", doc_description="the test value")
    assert "passes the test value" in pass_value.__doc__


def test_hiding_of_unset_sentinel_in_callbacks():
    """Fix: https://github.com/pallets/click/issues/3136"""

    def inspect_other_params(ctx, param, value):
        """A callback that inspects other parameters' values via the context."""
        assert click.get_current_context() is ctx
        click.echo(f"callback.my_arg: {ctx.params.get('my_arg')!r}")
        click.echo(f"callback.my_opt: {ctx.params.get('my_opt')!r}")
        click.echo(f"callback.my_callback: {ctx.params.get('my_callback')!r}")

        click.echo(f"callback.param: {param!r}")
        click.echo(f"callback.value: {value!r}")

        return "hard-coded"

    class ParameterInternalCheck(Option):
        """An option that checks internal state during processing."""

        def process_value(self, ctx, value):
            """Check that UNSET values are hidden as None in ctx.params within the
            callback, and then properly restored afterwards.
            """
            assert click.get_current_context() is ctx
            click.echo(f"before_process.my_arg: {ctx.params.get('my_arg')!r}")
            click.echo(f"before_process.my_opt: {ctx.params.get('my_opt')!r}")
            click.echo(f"before_process.my_callback: {ctx.params.get('my_callback')!r}")

            value = super().process_value(ctx, value)

            assert click.get_current_context() is ctx
            click.echo(f"after_process.my_arg: {ctx.params.get('my_arg')!r}")
            click.echo(f"after_process.my_opt: {ctx.params.get('my_opt')!r}")
            click.echo(f"after_process.my_callback: {ctx.params.get('my_callback')!r}")

            return value

    def change_other_params(ctx, param, value):
        """A callback that modifies other parameters' values via the context."""
        assert click.get_current_context() is ctx
        click.echo(f"before_change.my_arg: {ctx.params.get('my_arg')!r}")
        click.echo(f"before_change.my_opt: {ctx.params.get('my_opt')!r}")
        click.echo(f"before_change.my_callback: {ctx.params.get('my_callback')!r}")

        click.echo(f"before_change.param: {param!r}")
        click.echo(f"before_change.value: {value!r}")

        ctx.params["my_arg"] = "changed"
        # Reset to None parameters that where not UNSET to see they are not forced back
        # to UNSET.
        ctx.params["my_callback"] = None

        return value

    @click.command
    @click.argument("my-arg", required=False)
    @click.option("--my-opt")
    @click.option("--my-callback", callback=inspect_other_params)
    @click.option("--check-internal", cls=ParameterInternalCheck)
    @click.option(
        "--modifying-callback", cls=ParameterInternalCheck, callback=change_other_params
    )
    @click.pass_context
    def cli(ctx, my_arg, my_opt, my_callback, check_internal, modifying_callback):
        click.echo(f"cli.my_arg: {my_arg!r}")
        click.echo(f"cli.my_opt: {my_opt!r}")
        click.echo(f"cli.my_callback: {my_callback!r}")
        click.echo(f"cli.check_internal: {check_internal!r}")
        click.echo(f"cli.modifying_callback: {modifying_callback!r}")

    runner = click.testing.CliRunner()
    result = runner.invoke(cli)

    assert result.stdout.splitlines() == [
        # Values of other parameters within the callback are None, not UNSET.
        "callback.my_arg: None",
        "callback.my_opt: None",
        "callback.my_callback: None",
        "callback.param: <Option my_callback>",
        "callback.value: None",
        # Previous UNSET values were not altered by the callback.
        "before_process.my_arg: Sentinel.UNSET",
        "before_process.my_opt: Sentinel.UNSET",
        "before_process.my_callback: 'hard-coded'",
        "after_process.my_arg: Sentinel.UNSET",
        "after_process.my_opt: Sentinel.UNSET",
        "after_process.my_callback: 'hard-coded'",
        # Changes on other parameters within the callback are restored afterwards.
        "before_process.my_arg: Sentinel.UNSET",
        "before_process.my_opt: Sentinel.UNSET",
        "before_process.my_callback: 'hard-coded'",
        "before_change.my_arg: None",
        "before_change.my_opt: None",
        "before_change.my_callback: 'hard-coded'",
        "before_change.param: <ParameterInternalCheck modifying_callback>",
        "before_change.value: None",
        "after_process.my_arg: 'changed'",
        "after_process.my_opt: Sentinel.UNSET",
        "after_process.my_callback: None",
        # Unset values within the main command are UNSET, but hidden as None.
        "cli.my_arg: 'changed'",
        "cli.my_opt: None",
        "cli.my_callback: None",
        "cli.check_internal: None",
        "cli.modifying_callback: None",
    ]
    assert not result.stderr
    assert not result.exception
    assert result.exit_code == 0


def test_context_pushing():
    rv = []

    @click.command()
    def cli():
        pass

    ctx = click.Context(cli)

    @ctx.call_on_close
    def test_callback():
        rv.append(42)

    with ctx.scope(cleanup=False):
        # Internal
        assert ctx._depth == 2

    assert rv == []

    with ctx.scope():
        # Internal
        assert ctx._depth == 1

    assert rv == [42]


def test_pass_obj(runner):
    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj = "test"

    @cli.command()
    @click.pass_obj
    def test(obj):
        click.echo(obj)

    result = runner.invoke(cli, ["test"])
    assert not result.exception
    assert result.output == "test\n"


def test_close_before_pop(runner):
    called = []

    @click.command()
    @click.pass_context
    def cli(ctx):
        ctx.obj = "test"

        @ctx.call_on_close
        def foo():
            assert click.get_current_context().obj == "test"
            called.append(True)

        click.echo("aha!")

    result = runner.invoke(cli, [])
    assert not result.exception
    assert result.output == "aha!\n"
    assert called == [True]


def test_close_before_exit(runner):
    called = []

    @click.command()
    @click.pass_context
    def cli(ctx):
        ctx.obj = "test"

        @ctx.call_on_close
        def foo():
            assert click.get_current_context().obj == "test"
            called.append(True)

        ctx.exit()

        click.echo("aha!")

    result = runner.invoke(cli, [])
    assert not result.exception
    assert not result.output
    assert called == [True]


@pytest.mark.parametrize(
    ("cli_args", "expect"),
    [
        pytest.param(
            ("--option-with-callback", "--force-exit"),
            ["ExitingOption", "NonExitingOption"],
            id="natural_order",
        ),
        pytest.param(
            ("--force-exit", "--option-with-callback"),
            ["ExitingOption"],
            id="eagerness_precedence",
        ),
    ],
)
def test_multiple_eager_callbacks(runner, cli_args, expect):
    """Checks all callbacks are called on exit, even the nasty ones hidden within
    callbacks.

    Also checks the order in which they're called.
    """
    # Keeps track of callback calls.
    called = []

    class NonExitingOption(Option):
        def reset_state(self):
            called.append(self.__class__.__name__)

        def set_state(self, ctx: Context, param: Parameter, value: str) -> str:
            ctx.call_on_close(self.reset_state)
            return value

        def __init__(self, *args, **kwargs) -> None:
            kwargs.setdefault("expose_value", False)
            kwargs.setdefault("callback", self.set_state)
            super().__init__(*args, **kwargs)

    class ExitingOption(NonExitingOption):
        def set_state(self, ctx: Context, param: Parameter, value: str) -> str:
            value = super().set_state(ctx, param, value)
            ctx.exit()
            return value

    @click.command()
    @click.option("--option-with-callback", is_eager=True, cls=NonExitingOption)
    @click.option("--force-exit", is_eager=True, cls=ExitingOption)
    def cli():
        click.echo("This will never be printed as we forced exit via --force-exit")

    result = runner.invoke(cli, cli_args)
    assert not result.exception
    assert not result.output

    assert called == expect


def test_no_state_leaks(runner):
    """Demonstrate state leaks with a specific case of the generic test above.

    Use a logger as a real-world example of a common fixture which, due to its global
    nature, can leak state if not clean-up properly in a callback.
    """
    # Keeps track of callback calls.
    called = []

    class DebugLoggerOption(Option):
        """A custom option to set the name of the debug logger."""

        logger_name: str
        """The ID of the logger to use."""

        def reset_loggers(self):
            """Forces logger managed by the option to be reset to the default level."""
            logger = logging.getLogger(self.logger_name)
            logger.setLevel(logging.NOTSET)

            # Logger has been properly reset to its initial state.
            assert logger.level == logging.NOTSET
            assert logger.getEffectiveLevel() == logging.WARNING

            called.append(True)

        def set_level(self, ctx: Context, param: Parameter, value: str) -> None:
            """Set the logger to DEBUG level."""
            # Keep the logger name around so we can reset it later when winding down
            # the option.
            self.logger_name = value

            # Get the global logger object.
            logger = logging.getLogger(self.logger_name)

            # Check pre-conditions: new logger is not set, but inherits its level from
            # default <root> logger. That's the exact same state we are expecting our
            # logger to be in after being messed with by the CLI.
            assert logger.level == logging.NOTSET
            assert logger.getEffectiveLevel() == logging.WARNING

            logger.setLevel(logging.DEBUG)
            ctx.call_on_close(self.reset_loggers)
            return value

        def __init__(self, *args, **kwargs) -> None:
            kwargs.setdefault("callback", self.set_level)
            super().__init__(*args, **kwargs)

    @click.command()
    @click.option("--debug-logger-name", is_eager=True, cls=DebugLoggerOption)
    @help_option()
    @click.pass_context
    def messing_with_logger(ctx, debug_logger_name):
        # Introspect context to make sure logger name are aligned.
        assert debug_logger_name == ctx.command.params[0].logger_name

        logger = logging.getLogger(debug_logger_name)

        # Logger's level has been properly set to DEBUG by DebugLoggerOption.
        assert logger.level == logging.DEBUG
        assert logger.getEffectiveLevel() == logging.DEBUG

        logger.debug("Blah blah blah")

        ctx.exit()

        click.echo("This will never be printed as we exited early")

    # Call the CLI to mess with the custom logger.
    result = runner.invoke(
        messing_with_logger, ["--debug-logger-name", "my_logger", "--help"]
    )

    assert called == [True]

    # Check the custom logger has been reverted to it initial state by the option
    # callback after being messed with by the CLI.
    logger = logging.getLogger("my_logger")
    assert logger.level == logging.NOTSET
    assert logger.getEffectiveLevel() == logging.WARNING

    assert not result.exception
    assert result.output.startswith("Usage: messing-with-logger [OPTIONS]")


def test_with_resource():
    @contextmanager
    def manager():
        val = [1]
        yield val
        val[0] = 0

    ctx = click.Context(click.Command("test"))

    with ctx.scope():
        rv = ctx.with_resource(manager())
        assert rv[0] == 1

    assert rv == [0]


def test_with_resource_exception() -> None:
    class TestContext(AbstractContextManager[list[int]]):
        _handle_exception: bool
        _base_val: int
        val: list[int]

        def __init__(self, base_val: int = 1, *, handle_exception: bool = True) -> None:
            self._handle_exception = handle_exception
            self._base_val = base_val

        def __enter__(self) -> list[int]:
            self.val = [self._base_val]
            return self.val

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            if not exc_type:
                self.val[0] = self._base_val - 1
                return None

            self.val[0] = self._base_val + 1
            return self._handle_exception

    class TestException(Exception):
        pass

    ctx = click.Context(click.Command("test"))

    base_val = 1

    with ctx.scope():
        rv = ctx.with_resource(TestContext(base_val=base_val))
        assert rv[0] == base_val

    assert rv == [base_val - 1]

    with ctx.scope():
        rv = ctx.with_resource(TestContext(base_val=base_val))
        raise TestException()

    assert rv == [base_val + 1]

    with pytest.raises(TestException):
        with ctx.scope():
            rv = ctx.with_resource(
                TestContext(base_val=base_val, handle_exception=False)
            )
            raise TestException()


def test_with_resource_nested_exception() -> None:
    class TestContext(AbstractContextManager[list[int]]):
        _handle_exception: bool
        _base_val: int
        val: list[int]

        def __init__(self, base_val: int = 1, *, handle_exception: bool = True) -> None:
            self._handle_exception = handle_exception
            self._base_val = base_val

        def __enter__(self) -> list[int]:
            self.val = [self._base_val]
            return self.val

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            if not exc_type:
                self.val[0] = self._base_val - 1
                return None

            self.val[0] = self._base_val + 1
            return self._handle_exception

    class TestException(Exception):
        pass

    ctx = click.Context(click.Command("test"))
    base_val = 1
    base_val_nested = 11

    with ctx.scope():
        rv = ctx.with_resource(TestContext(base_val=base_val))
        rv_nested = ctx.with_resource(TestContext(base_val=base_val_nested))
        assert rv[0] == base_val
        assert rv_nested[0] == base_val_nested

    assert rv == [base_val - 1]
    assert rv_nested == [base_val_nested - 1]

    with ctx.scope():
        rv = ctx.with_resource(TestContext(base_val=base_val))
        rv_nested = ctx.with_resource(TestContext(base_val=base_val_nested))
        raise TestException()

    # If one of the context "eats" the exceptions they will not be forwarded to other
    # parts. This is due to how ExitStack unwinding works
    assert rv_nested == [base_val_nested + 1]
    assert rv == [base_val - 1]

    with ctx.scope():
        rv = ctx.with_resource(TestContext(base_val=base_val))
        rv_nested = ctx.with_resource(
            TestContext(base_val=base_val_nested, handle_exception=False)
        )
        raise TestException()

    assert rv_nested == [base_val_nested + 1]
    assert rv == [base_val + 1]

    with pytest.raises(TestException):
        rv = ctx.with_resource(TestContext(base_val=base_val, handle_exception=False))
        rv_nested = ctx.with_resource(
            TestContext(base_val=base_val_nested, handle_exception=False)
        )
        raise TestException()


def _raise(exc: BaseException) -> None:
    raise exc


def test_close_single_failure_is_typed_and_attributed(runner) -> None:
    @click.command()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(ValueError("disk full")))

    result = runner.invoke(cli, [])

    assert isinstance(result.exception, click.CleanupError)
    assert result.exit_code == 1
    failure = result.exception.failures[0]
    assert len(result.exception.failures) == 1
    assert failure.exception_type == "ValueError"
    assert failure.exception_message == "disk full"
    assert failure.command_path == "cli"
    assert "cli" in failure.source and "lambda" in failure.source
    message = str(result.exception)
    assert "while closing" in message
    assert "disk full" in message
    # The aggregated error chains from the original failure for tracebacks.
    assert result.exception.__cause__ is failure.exception


def test_close_failure_nonstandalone_matches_standalone(runner) -> None:
    @click.command()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(ValueError("disk full")))

    result = runner.invoke(cli, [], standalone_mode=False)
    assert isinstance(result.exception, click.CleanupError)
    assert result.exception.failures[0].exception_message == "disk full"


def test_close_runs_every_callback_and_lists_every_failure(runner) -> None:
    ran: list[str] = []

    def make_failing_callback(tag: str) -> t.Callable[[], None]:
        def callback() -> None:
            ran.append(tag)
            raise RuntimeError(f"boom {tag}")

        return callback

    @click.command()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(make_failing_callback("first"))
        ctx.call_on_close(make_failing_callback("second"))
        ctx.call_on_close(make_failing_callback("third"))

    result = runner.invoke(cli, [])

    # LIFO order is preserved, and one failure does not skip the rest.
    assert ran == ["third", "second", "first"]
    assert isinstance(result.exception, click.CleanupError)
    failures = result.exception.failures
    assert [f.exception_message for f in failures] == [
        "boom third",
        "boom second",
        "boom first",
    ]
    assert {f.command_path for f in failures} == {"cli"}
    assert all(f.exception_type == "RuntimeError" for f in failures)
    assert "3 cleanup handlers failed" in str(result.exception)


def test_close_failure_after_success_does_not_replace_business_exception() -> None:
    class BusinessError(Exception):
        pass

    ctx = click.Context(click.Command("test"))

    with pytest.raises(BusinessError) as exc_info:
        with ctx.scope():
            ctx.call_on_close(lambda: _raise(RuntimeError("cleanup boom")))
            raise BusinessError("business boom")

    exc = exc_info.value
    # The body exception is the primary conclusion: its type is unchanged and
    # no cleanup exception is chained in front of it.
    assert str(exc) == "business boom"
    assert exc.__context__ is None
    (failure,) = exc.cleanup_failures
    assert isinstance(failure, click.CleanupFailure)
    assert failure.exception_message == "cleanup boom"
    assert failure.exception_type == "RuntimeError"
    # The context was created without an info name, so no command path is
    # available; the callback identity still carries the attribution.
    assert failure.command_path is None


def test_close_failure_keeps_business_exception_through_runner(runner) -> None:
    class BusinessError(Exception):
        pass

    @click.command()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(ValueError("cleanup boom")))
        raise BusinessError("business boom")

    result = runner.invoke(cli, [])

    assert type(result.exception) is BusinessError
    (failure,) = result.exception.cleanup_failures
    assert failure.command_path == "cli"
    assert failure.exception_message == "cleanup boom"
    assert not isinstance(result.exception, click.CleanupError)


def test_close_failure_nested_contexts_attributed_inner_to_outer(runner) -> None:
    class BusinessError(Exception):
        pass

    @click.group()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(RuntimeError("outer cleanup")))

    @cli.command()
    @click.pass_context
    def sub(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(RuntimeError("inner cleanup")))
        raise BusinessError("from subcommand")

    result = runner.invoke(cli, ["sub"])

    assert type(result.exception) is BusinessError
    failures = result.exception.cleanup_failures
    assert [f.command_path for f in failures] == ["cli sub", "cli"]
    assert [f.exception_message for f in failures] == [
        "inner cleanup",
        "outer cleanup",
    ]


def test_close_failure_nested_cleanup_only_merges_into_one_error(runner) -> None:
    @click.group()
    @click.pass_context
    def cli(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(RuntimeError("outer cleanup")))

    @cli.command()
    @click.pass_context
    def sub(ctx: Context) -> None:
        ctx.call_on_close(lambda: _raise(RuntimeError("inner cleanup")))

    result = runner.invoke(cli, ["sub"])

    assert isinstance(result.exception, click.CleanupError)
    failures = result.exception.failures
    assert [f.command_path for f in failures] == ["cli sub", "cli"]
    assert [f.exception_message for f in failures] == [
        "inner cleanup",
        "outer cleanup",
    ]
    assert "2 cleanup handlers failed" in str(result.exception)


def test_close_method_raises_cleanup_error() -> None:
    ctx = click.Context(click.Command("test"))
    ctx.call_on_close(lambda: _raise(KeyError("missing")))

    with pytest.raises(click.CleanupError) as exc_info:
        ctx.close()

    (failure,) = exc_info.value.failures
    assert failure.exception_type == "KeyError"
    assert failure.command_path is None
    assert "lambda" in failure.source


def test_close_resource_failure_is_attributed_as_resource() -> None:
    class BrokenResource(AbstractContextManager[None]):
        name = "artifact.bin"

        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> bool | None:
            raise OSError("cannot flush")

    ctx = click.Context(click.Command("test"))
    ctx.with_resource(BrokenResource())

    with pytest.raises(click.CleanupError) as exc_info:
        ctx.close()

    (failure,) = exc_info.value.failures
    assert "BrokenResource" in failure.source
    assert "artifact.bin" in failure.source
    assert failure.exception_type == "OSError"
    assert failure.exception_message == "cannot flush"


def test_close_resource_failure_attaches_to_business_exception() -> None:
    class BusinessError(Exception):
        pass

    class BrokenResource(AbstractContextManager[None]):
        seen_exc: BaseException | None = None

        def __enter__(self) -> None:
            return None

        def __exit__(self, et, exc, tb) -> bool | None:
            # The resource still observes the body exception, not another
            # resource's cleanup failure.
            BrokenResource.seen_exc = exc
            raise OSError("exit failed")

    ctx = click.Context(click.Command("test"))

    with pytest.raises(BusinessError) as exc_info:
        with ctx.scope():
            ctx.with_resource(BrokenResource())
            raise BusinessError("body failed")

    assert isinstance(BrokenResource.seen_exc, BusinessError)
    (failure,) = exc_info.value.cleanup_failures
    assert failure.exception_type == "OSError"
    assert "BrokenResource" in failure.source


def test_failed_close_pops_context_and_leaves_reusable_stack(runner) -> None:
    seen: list[str] = []

    @click.command()
    @click.pass_context
    def cli(ctx: Context) -> None:
        def failing_callback() -> None:
            # The context is still the current one while the callback runs.
            seen.append(click.get_current_context().command.name)
            raise RuntimeError("boom")

        ctx.call_on_close(failing_callback)

    result = runner.invoke(cli, [])
    assert isinstance(result.exception, click.CleanupError)
    assert seen == ["cli"]
    # A failed close must not leave its context on the thread-local stack.
    assert click.get_current_context(silent=True) is None

    # A context is reusable after a failed close: the registry was reset.
    ctx = click.Context(click.Command("test"))
    ran: list[str] = []
    ctx.call_on_close(lambda: (_ for _ in ()).throw(RuntimeError("first")))
    with pytest.raises(click.CleanupError):
        ctx.close()
    ctx.call_on_close(lambda: ran.append("second"))
    ctx.close()
    assert ran == ["second"]


def test_close_failure_with_bound_callback_uses_qualified_name() -> None:
    class Service:
        def shutdown(self) -> None:
            raise RuntimeError("shutdown failed")

    service = Service()
    ctx = click.Context(click.Command("test"))
    ctx.call_on_close(service.shutdown)

    with pytest.raises(click.CleanupError) as exc_info:
        ctx.close()

    (failure,) = exc_info.value.failures
    assert "Service.shutdown" in failure.source


def test_make_pass_decorator_args(runner):
    """
    Test to check that make_pass_decorator doesn't consume arguments based on
    invocation order.
    """

    class Foo:
        title = "foocmd"

    pass_foo = click.make_pass_decorator(Foo)

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj = Foo()

    @cli.command()
    @click.pass_context
    @pass_foo
    def test1(foo, ctx):
        click.echo(foo.title)

    @cli.command()
    @pass_foo
    @click.pass_context
    def test2(ctx, foo):
        click.echo(foo.title)

    result = runner.invoke(cli, ["test1"])
    assert not result.exception
    assert result.output == "foocmd\n"

    result = runner.invoke(cli, ["test2"])
    assert not result.exception
    assert result.output == "foocmd\n"


def test_propagate_show_default_setting(runner):
    """A context's ``show_default`` setting defaults to the value from
    the parent context.
    """
    group = click.Group(
        commands={
            "sub": click.Command("sub", params=[click.Option(["-a"], default="a")]),
        },
        context_settings={"show_default": True},
    )
    result = runner.invoke(group, ["sub", "--help"])
    assert "[default: a]" in result.output


def test_exit_not_standalone():
    @click.command()
    @click.pass_context
    def cli(ctx):
        ctx.exit(1)

    assert cli.main([], "test_exit_not_standalone", standalone_mode=False) == 1

    @click.command()
    @click.pass_context
    def cli(ctx):
        ctx.exit(0)

    assert cli.main([], "test_exit_not_standalone", standalone_mode=False) == 0


@pytest.mark.parametrize(
    ("option_args", "invoke_args", "expect"),
    [
        pytest.param({}, {}, ParameterSource.DEFAULT, id="default"),
        pytest.param(
            {},
            {"default_map": {"option": 1}},
            ParameterSource.DEFAULT_MAP,
            id="default_map",
        ),
        pytest.param(
            {},
            {"args": ["-o", "1"]},
            ParameterSource.COMMANDLINE,
            id="commandline short",
        ),
        pytest.param(
            {},
            {"args": ["--option", "1"]},
            ParameterSource.COMMANDLINE,
            id="commandline long",
        ),
        pytest.param(
            {},
            {"auto_envvar_prefix": "TEST", "env": {"TEST_OPTION": "1"}},
            ParameterSource.ENVIRONMENT,
            id="environment auto",
        ),
        pytest.param(
            {"envvar": "NAME"},
            {"env": {"NAME": "1"}},
            ParameterSource.ENVIRONMENT,
            id="environment manual",
        ),
    ],
)
def test_parameter_source(runner, option_args, invoke_args, expect):
    @click.command()
    @click.pass_context
    @click.option("-o", "--option", default=1, **option_args)
    def cli(ctx, option):
        return ctx.get_parameter_source("option")

    rv = runner.invoke(cli, standalone_mode=False, **invoke_args)
    assert rv.return_value == expect


def test_propagate_opt_prefixes():
    parent = click.Context(click.Command("test"))
    parent._opt_prefixes = {"-", "--", "!"}
    ctx = click.Context(click.Command("test2"), parent=parent)

    assert ctx._opt_prefixes == {"-", "--", "!"}
