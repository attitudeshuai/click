import sys

import pytest

import click


def debug():
    click.echo(
        f"{sys._getframe(1).f_code.co_name}"
        f"={'|'.join(click.get_current_context().args)}"
    )


def test_basic_chaining(runner):
    @click.group(chain=True)
    def cli():
        pass

    @cli.command("sdist")
    def sdist():
        click.echo("sdist called")

    @cli.command("bdist")
    def bdist():
        click.echo("bdist called")

    result = runner.invoke(cli, ["bdist", "sdist", "bdist"])
    assert not result.exception
    assert result.output.splitlines() == [
        "bdist called",
        "sdist called",
        "bdist called",
    ]


@pytest.mark.parametrize(
    ("args", "expect"),
    [
        (["--help"], "COMMAND1 [ARGS]... [COMMAND2 [ARGS]...]..."),
        (["--help"], "ROOT HELP"),
        (["sdist", "--help"], "SDIST HELP"),
        (["bdist", "--help"], "BDIST HELP"),
        (["bdist", "sdist", "--help"], "SDIST HELP"),
    ],
)
def test_chaining_help(runner, args, expect):
    @click.group(chain=True)
    def cli():
        """ROOT HELP"""
        pass

    @cli.command("sdist")
    def sdist():
        """SDIST HELP"""
        click.echo("sdist called")

    @cli.command("bdist")
    def bdist():
        """BDIST HELP"""
        click.echo("bdist called")

    result = runner.invoke(cli, args)
    assert not result.exception
    assert expect in result.output


def test_chaining_with_options(runner):
    @click.group(chain=True)
    def cli():
        pass

    @cli.command("sdist")
    @click.option("--format")
    def sdist(format):
        click.echo(f"sdist called {format}")

    @cli.command("bdist")
    @click.option("--format")
    def bdist(format):
        click.echo(f"bdist called {format}")

    result = runner.invoke(cli, ["bdist", "--format=1", "sdist", "--format=2"])
    assert not result.exception
    assert result.output.splitlines() == ["bdist called 1", "sdist called 2"]


@pytest.mark.parametrize(("chain", "expect"), [(False, "1"), (True, "[]")])
def test_no_command_result_callback(runner, chain, expect):
    """When a group has ``invoke_without_command=True``, the result
    callback is always invoked. A regular group invokes it with
    its return value, a chained group with ``[]``.
    """

    @click.group(invoke_without_command=True, chain=chain)
    def cli():
        return 1

    @cli.result_callback()
    def process_result(result):
        click.echo(result, nl=False)

    result = runner.invoke(cli, [])
    assert result.output == expect


def test_chaining_with_arguments(runner):
    @click.group(chain=True)
    def cli():
        pass

    @cli.command("sdist")
    @click.argument("format")
    def sdist(format):
        click.echo(f"sdist called {format}")

    @cli.command("bdist")
    @click.argument("format")
    def bdist(format):
        click.echo(f"bdist called {format}")

    result = runner.invoke(cli, ["bdist", "1", "sdist", "2"])
    assert not result.exception
    assert result.output.splitlines() == ["bdist called 1", "sdist called 2"]


@pytest.mark.parametrize(
    ("args", "input", "expect"),
    [
        (["-f", "-"], "foo\nbar", ["foo", "bar"]),
        (["-f", "-", "strip"], "foo \n bar", ["foo", "bar"]),
        (["-f", "-", "strip", "uppercase"], "foo \n bar", ["FOO", "BAR"]),
    ],
)
def test_pipeline(runner, args, input, expect):
    @click.group(chain=True, invoke_without_command=True)
    @click.option("-f", type=click.File("r"))
    def cli(f):
        pass

    @cli.result_callback()
    def process_pipeline(processors, f):
        iterator = (x.rstrip("\r\n") for x in f)
        for processor in processors:
            iterator = processor(iterator)
        for item in iterator:
            click.echo(item)

    @cli.command("uppercase")
    def make_uppercase():
        def processor(iterator):
            for line in iterator:
                yield line.upper()

        return processor

    @cli.command("strip")
    def make_strip():
        def processor(iterator):
            for line in iterator:
                yield line.strip()

        return processor

    result = runner.invoke(cli, args, input=input)
    assert not result.exception
    assert result.output.splitlines() == expect


def test_args_and_chain(runner):
    @click.group(chain=True)
    def cli():
        debug()

    @cli.command()
    def a():
        debug()

    @cli.command()
    def b():
        debug()

    @cli.command()
    def c():
        debug()

    result = runner.invoke(cli, ["a", "b", "c"])
    assert not result.exception
    assert result.output.splitlines() == ["cli=", "a=", "b=", "c="]


def test_group_arg_behavior(runner):
    with pytest.raises(RuntimeError):

        @click.group(chain=True)
        @click.argument("forbidden", required=False)
        def bad_cli():
            pass

    with pytest.raises(RuntimeError):

        @click.group(chain=True)
        @click.argument("forbidden", nargs=-1)
        def bad_cli2():
            pass

    @click.group(chain=True)
    @click.argument("arg")
    def cli(arg):
        click.echo(f"cli:{arg}")

    @cli.command()
    def a():
        click.echo("a")

    result = runner.invoke(cli, ["foo", "a"])
    assert not result.exception
    assert result.output.splitlines() == ["cli:foo", "a"]


def test_chain_failure_policy_configuration():
    # The default policy is abort, for chained and regular groups.
    assert click.Group(chain=True).chain_failure_policy is (
        click.ChainFailurePolicy.ABORT
    )
    assert click.Group().chain_failure_policy is click.ChainFailurePolicy.ABORT

    # Enum members and their string values are both accepted.
    group = click.Group(
        chain=True,
        chain_failure_policy=click.ChainFailurePolicy.CONTINUE,
    )
    assert group.chain_failure_policy is click.ChainFailurePolicy.CONTINUE
    group = click.Group(chain=True, chain_failure_policy="skip_remaining")
    assert group.chain_failure_policy is (
        click.ChainFailurePolicy.SKIP_REMAINING
    )
    assert group.chain_failure_policy == "skip_remaining"

    # A non-default policy requires a chained group.
    with pytest.raises(RuntimeError, match="chain mode"):
        click.Group(chain_failure_policy="continue")

    # Invalid policy values are rejected.
    with pytest.raises(ValueError, match="chain_failure_policy"):
        click.Group(chain=True, chain_failure_policy="nope")


def test_chain_result_basic():
    success = click.ChainResult("a", click.ChainResultStatus.SUCCESS, value=1)
    assert success.ok
    assert success.name == "a"
    assert success.value == 1
    assert success.exception is None

    error = RuntimeError("boom")
    failure = click.ChainResult(
        "b", click.ChainResultStatus.FAILURE, exception=error
    )
    assert not failure.ok
    assert failure.value is None
    assert failure.exception is error

    skipped = click.ChainResult("c", click.ChainResultStatus.SKIPPED)
    assert skipped.status == "skipped"
    assert not skipped.ok
    assert skipped.value is None
    assert skipped.exception is None

    assert click.ChainResultStatus("not_run") is click.ChainResultStatus.NOT_RUN


def test_chain_abort_policy_default_unchanged(runner):
    ran = []
    callback_ran = []

    @click.group(chain=True)
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")

    @cli.command()
    def b():
        ran.append("b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        ran.append("c")

    @cli.result_callback()
    def process_result(results):
        callback_ran.append(results)

    result = runner.invoke(cli, ["a", "b", "c"])

    # The first failure stops the chain, c never runs, the original
    # exception propagates unchanged, and the callback is not invoked.
    assert ran == ["a", "b"]
    assert callback_ran == []
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "boom"
    assert result.exit_code == 1


def test_chain_abort_policy_outcomes_callback(runner):
    seen = []

    @click.group(chain=True)
    def cli():
        pass

    @cli.command()
    def a():
        return 1

    @cli.command()
    def b():
        raise RuntimeError("boom")

    @cli.command()
    def c():
        return 3

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen.append(results)

    result = runner.invoke(cli, ["a", "b", "c"])

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "boom"
    assert result.exit_code == 1

    (outcomes,) = seen
    assert [(r.name, r.status) for r in outcomes] == [
        ("a", click.ChainResultStatus.SUCCESS),
        ("b", click.ChainResultStatus.FAILURE),
        ("c", click.ChainResultStatus.NOT_RUN),
    ]
    assert outcomes[0].value == 1
    # The exception object is the original, neither wrapped nor rewritten.
    assert outcomes[1].exception is result.exception


def test_chain_continue_policy_runs_all(runner):
    @click.group(
        chain=True,
        chain_failure_policy=click.ChainFailurePolicy.CONTINUE,
    )
    def cli():
        pass

    @cli.command()
    def a():
        click.echo("a")
        return 1

    error = RuntimeError("boom")

    @cli.command()
    def b():
        click.echo("b")
        raise error

    @cli.command()
    def c():
        click.echo("c")
        return 3

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b", "c"])

    # Every command ran in order.
    assert result.output.splitlines() == ["a", "b", "c"]
    assert result.exit_code == 1

    outcomes = seen["results"]
    assert [r.name for r in outcomes] == ["a", "b", "c"]
    assert [r.status for r in outcomes] == [
        click.ChainResultStatus.SUCCESS,
        click.ChainResultStatus.FAILURE,
        click.ChainResultStatus.SUCCESS,
    ]
    assert outcomes[0].value == 1
    assert outcomes[1].exception is error
    assert type(outcomes[1].exception) is RuntimeError
    assert str(outcomes[1].exception) == "boom"
    assert outcomes[2].value == 3


def test_chain_continue_policy_multiple_failures(runner):
    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        raise ValueError("a bad")

    @cli.command()
    def b():
        raise KeyError("b bad")

    @cli.command()
    def c():
        return "ok"

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b", "c"])

    assert result.exit_code == 1
    outcomes = seen["results"]
    assert [r.status for r in outcomes] == [
        click.ChainResultStatus.FAILURE,
        click.ChainResultStatus.FAILURE,
        click.ChainResultStatus.SUCCESS,
    ]
    assert type(outcomes[0].exception) is ValueError
    assert type(outcomes[1].exception) is KeyError
    assert outcomes[2].value == "ok"


def test_chain_continue_policy_all_success(runner):
    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        return 1

    @cli.command()
    def b():
        return 2

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b"])

    assert result.exit_code == 0
    outcomes = seen["results"]
    assert all(r.ok for r in outcomes)
    assert [r.value for r in outcomes] == [1, 2]


def test_chain_skip_remaining_policy(runner):
    ran = []

    @click.group(chain=True, chain_failure_policy="skip_remaining")
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")
        return 1

    @cli.command()
    def b():
        ran.append("b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        ran.append("c")
        return 3

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b", "c"])

    assert ran == ["a", "b"]
    assert result.exit_code == 1
    outcomes = seen["results"]
    assert [(r.name, r.status) for r in outcomes] == [
        ("a", click.ChainResultStatus.SUCCESS),
        ("b", click.ChainResultStatus.FAILURE),
        ("c", click.ChainResultStatus.SKIPPED),
    ]
    assert outcomes[2].value is None
    assert outcomes[2].exception is None


def test_chain_skip_remaining_passes_string_policy(runner):
    @click.group(chain=True, chain_failure_policy="skip_remaining")
    def cli():
        pass

    @cli.command()
    def a():
        return 1

    @cli.command()
    def b():
        raise RuntimeError("boom")

    @cli.command()
    def c():
        return 3

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b", "c"])
    assert result.exit_code == 1
    assert [r.status for r in seen["results"]] == [
        click.ChainResultStatus.SUCCESS,
        click.ChainResultStatus.FAILURE,
        click.ChainResultStatus.SKIPPED,
    ]


def test_chain_legacy_result_callback_compat(runner):
    # An old-style callback keeps receiving a plain list of return values,
    # with None for commands that did not succeed.
    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        return 1

    @cli.command()
    def b():
        raise RuntimeError("boom")

    @cli.command()
    def c():
        return 3

    seen = {}

    @cli.result_callback()
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b", "c"])

    assert result.exit_code == 1
    assert seen["results"] == [1, None, 3]


def test_chain_outcomes_callback_default_policy_success(runner):
    # Under the default policy, an outcomes callback on a successful chain
    # gets the upgraded list while execution behaves as before.
    @click.group(chain=True)
    def cli():
        pass

    @cli.command()
    def a():
        return "x"

    @cli.command()
    def b():
        return "y"

    seen = {}

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen["results"] = results

    result = runner.invoke(cli, ["a", "b"])

    assert result.exit_code == 0
    outcomes = seen["results"]
    assert [(r.name, r.status, r.value) for r in outcomes] == [
        ("a", click.ChainResultStatus.SUCCESS, "x"),
        ("b", click.ChainResultStatus.SUCCESS, "y"),
    ]


def test_chain_cleanup_per_command_under_continue(runner):
    events = []

    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        click.get_current_context().call_on_close(lambda: events.append("close-a"))
        events.append("run-a")

    @cli.command()
    def b():
        click.get_current_context().call_on_close(lambda: events.append("close-b"))
        events.append("run-b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        click.get_current_context().call_on_close(lambda: events.append("close-c"))
        events.append("run-c")

    result = runner.invoke(cli, ["a", "b", "c"])

    assert result.exit_code == 1
    # Each context is reclaimed when its command ends; a later failure
    # does not undo the cleanup of commands that already finished.
    assert events == [
        "run-a",
        "close-a",
        "run-b",
        "close-b",
        "run-c",
        "close-c",
    ]


def test_chain_cleanup_under_abort(runner):
    events = []

    @click.group(chain=True)
    def cli():
        pass

    @cli.command()
    def a():
        click.get_current_context().call_on_close(lambda: events.append("close-a"))
        events.append("run-a")

    @cli.command()
    def b():
        events.append("run-b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        events.append("run-c")

    result = runner.invoke(cli, ["a", "b", "c"])

    assert result.exit_code == 1
    # a was reclaimed before b failed, even though the chain aborts.
    assert events == ["run-a", "close-a", "run-b"]


def test_chain_skipped_context_is_reclaimed(runner):
    # A skipped command never runs, but resources its context acquired
    # while parsing are still released.
    events = []

    def register_close(ctx, param, value):
        ctx.call_on_close(lambda: events.append("close-c"))

    @click.group(chain=True, chain_failure_policy="skip_remaining")
    def cli():
        pass

    @cli.command()
    def a():
        events.append("run-a")

    @cli.command()
    def b():
        events.append("run-b")
        raise RuntimeError("boom")

    @cli.command()
    @click.option(
        "--x",
        is_eager=True,
        is_flag=True,
        expose_value=False,
        callback=register_close,
    )
    def c():
        events.append("run-c")

    result = runner.invoke(cli, ["a", "b", "c", "--x"])

    assert result.exit_code == 1
    assert "run-c" not in events
    assert "close-c" in events


def test_chain_skipped_commands_have_no_side_effects(runner):
    ran = []

    @click.group(chain=True, chain_failure_policy="skip_remaining")
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")

    @cli.command()
    def b():
        ran.append("b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        click.echo("c must not be echoed")
        ran.append("c")

    result = runner.invoke(cli, ["a", "b", "c"])

    assert ran == ["a", "b"]
    assert "c must not be echoed" not in result.output


def test_chain_continue_without_result_callback(runner):
    ran = []

    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")

    @cli.command()
    def b():
        ran.append("b")
        raise RuntimeError("boom")

    @cli.command()
    def c():
        ran.append("c")

    result = runner.invoke(cli, ["a", "b", "c"])

    assert ran == ["a", "b", "c"]
    assert result.exit_code == 1


def test_chain_control_signal_exit_propagates(runner):
    ran = []

    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")

    @cli.command()
    def b():
        ran.append("b")
        click.get_current_context().exit(0)

    @cli.command()
    def c():
        ran.append("c")

    seen = []

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen.append(results)

    result = runner.invoke(cli, ["a", "b", "c"])

    # An explicit exit is a control signal, not a recorded failure, so the
    # policy does not override it and the remaining command does not run.
    assert result.exit_code == 0
    assert ran == ["a", "b"]
    assert seen == []


def test_chain_control_signal_abort_propagates(runner):
    ran = []

    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        pass

    @cli.command()
    def a():
        ran.append("a")

    @cli.command()
    def b():
        ran.append("b")
        raise click.Abort()

    @cli.command()
    def c():
        ran.append("c")

    result = runner.invoke(cli, ["a", "b", "c"])

    assert ran == ["a", "b"]
    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_chain_failure_policy_no_args_is_help(runner):
    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        """ROOT HELP"""
        pass

    @cli.command()
    def a():
        pass

    result = runner.invoke(cli, [])

    # no_args_is_help still wins for an empty chain.
    assert result.exit_code == 2
    assert "ROOT HELP" in result.output


def test_chain_failure_policy_invoke_without_command(runner):
    seen = []

    @click.group(
        chain=True,
        invoke_without_command=True,
        chain_failure_policy="continue",
    )
    def cli():
        return 1

    @cli.result_callback(outcomes=True)
    def process_result(results):
        seen.append(results)

    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    # No subcommands means an empty list, as without a failure policy.
    assert seen == [[]]


def test_chain_failure_policy_does_not_cover_group_callback(runner):
    @click.group(chain=True, chain_failure_policy="continue")
    def cli():
        raise RuntimeError("group boom")

    @cli.command()
    def a():
        return 1

    result = runner.invoke(cli, ["a"])

    # The group's own callback failure still propagates unchanged.
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "group boom"


def test_non_chain_group_unaffected(runner):
    seen = []

    @click.group()
    def cli():
        pass

    @cli.command()
    def a():
        return 1

    @cli.result_callback()
    def process_result(result):
        # The non-chained semantics are unchanged: a single value, not a
        # list of outcomes.
        seen.append(result)

    result = runner.invoke(cli, ["a"])

    assert result.exit_code == 0
    assert seen == [1]


def test_non_chain_group_failure_unaffected(runner):
    @click.group()
    def cli():
        pass

    @cli.command()
    def a():
        raise RuntimeError("boom")

    callback_ran = []

    @cli.result_callback()
    def process_result(result):
        callback_ran.append(result)

    result = runner.invoke(cli, ["a"])

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "boom"
    assert callback_ran == []


@pytest.mark.xfail
def test_group_chaining(runner):
    @click.group(chain=True)
    def cli():
        debug()

    @cli.group()
    def l1a():
        debug()

    @l1a.command()
    def l2a():
        debug()

    @l1a.command()
    def l2b():
        debug()

    @cli.command()
    def l1b():
        debug()

    result = runner.invoke(cli, ["l1a", "l2a", "l1b"])
    assert not result.exception
    assert result.output.splitlines() == ["cli=", "l1a=", "l2a=", "l1b="]
