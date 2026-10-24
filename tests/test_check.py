"""Tests for the explicit ``default_map`` / ``context_settings`` check."""

from __future__ import annotations

import io

import pytest

import click
from click.checking import CONTEXT_SETTINGS
from click.checking import DEFAULT_MAP
from click.checking import MISPLACED_TABLE
from click.checking import UNKNOWN_KEY

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def build_tree() -> tuple[click.Group, click.Command]:
    """``cli`` (``--debug``) -> ``db`` -> ``init`` (``--verbose``)."""

    @click.group()
    @click.option("--debug", is_flag=True)
    def cli(debug):
        pass

    @cli.group()
    def db():
        pass

    @db.command()
    @click.option("--verbose", is_flag=True)
    def init(verbose):
        click.echo(f"verbose={verbose}")

    return cli, init


# ---------------------------------------------------------------------------
# Valid configurations report nothing
# ---------------------------------------------------------------------------


def test_clean_tree_no_issues_no_output(capsys):
    cli, _ = build_tree()

    issues = click.check_command(
        cli,
        default_map={"debug": True, "db": {"init": {"verbose": True}}},
    )

    assert issues == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_empty_command_and_none_default_map(capsys):
    cmd = click.Command("plain")

    assert click.check_command(cmd) == []
    assert click.check_command(cmd, default_map=None) == []
    assert click.check_command(cmd, default_map={}) == []

    captured = capsys.readouterr()
    assert captured.err == ""


def test_valid_context_settings_pass():
    cmd = click.Command(
        "plain",
        context_settings={
            "help_option_names": ["-h"],
            "ignore_unknown_options": True,
            "allow_extra_args": True,
            "allow_interspersed_args": False,
            "color": True,
            "terminal_width": 100,
            "max_content_width": 80,
            "auto_envvar_prefix": "APP",
            "obj": {},
            "resilient_parsing": False,
            "show_default": True,
            "token_normalize_func": str.lower,
            "default_map": {},
        },
    )

    assert click.check_command(cmd) == []


def test_mapping_value_for_parameter_is_not_a_nested_table():
    @click.command()
    @click.option("--config")
    def cli(config):
        pass

    issues = click.check_command(cli, default_map={"config": {"a": 1, "b": 2}})

    assert issues == []


def test_default_map_values_are_not_type_checked():
    @click.command()
    @click.option("--port", type=int)
    def cli(port):
        pass

    # Only keys are checked; an incompatible value is not this check's job.
    assert click.check_command(cli, default_map={"port": "not-an-int"}) == []


def test_hidden_command_nested_table_is_legal():
    cli = click.Group("cli")
    hidden = click.Command("secret", params=[click.Option(["--token"])])
    cli.add_command(hidden)
    hidden.hidden = True

    assert click.check_command(cli, default_map={"secret": {"token": "x"}}) == []


def test_command_registration_alias_is_the_legal_key():
    cli = click.Group("cli")
    cmd = click.Command(
        "original", params=[click.Option(["--verbose", "-v"], is_flag=True)]
    )
    cli.add_command(cmd, name="alt")

    assert click.check_command(cli, default_map={"alt": {"verbose": True}}) == []

    issues = click.check_command(cli, default_map={"original": {"verbose": True}})
    assert len(issues) == 1
    assert issues[0].key == "original"
    assert issues[0].command_path == ("cli",)


def test_chain_group_traversal():
    @click.group(chain=True)
    def cli():
        pass

    @cli.command()
    @click.option("--verbose", is_flag=True)
    def step(verbose):
        pass

    assert click.check_command(cli, default_map={"step": {"verbose": True}}) == []
    issues = click.check_command(cli, default_map={"step": {"verbos": True}})
    assert len(issues) == 1
    assert issues[0].command_path == ("cli", "step")


# ---------------------------------------------------------------------------
# Unknown default_map keys
# ---------------------------------------------------------------------------


def test_unknown_default_map_key_is_reported_with_location():
    cli, _ = build_tree()

    issues = click.check_command(cli, default_map={"debig": True}, file=io.StringIO())

    assert len(issues) == 1
    issue = issues[0]
    assert issue.category is DEFAULT_MAP
    assert issue.kind is UNKNOWN_KEY
    assert issue.key == "debig"
    assert issue.command_path == ("cli",)
    assert "cli" in str(issue)
    assert "'debig'" in str(issue)


def test_all_unknown_keys_are_reported_at_once():
    cli, _ = build_tree()

    issues = click.check_command(
        cli,
        default_map={"debig": True, "other": 1, "db": {"itni": {"verbose": True}}},
        file=io.StringIO(),
    )

    keys = {(issue.command_path, issue.key) for issue in issues}
    assert keys == {
        (("cli",), "debig"),
        (("cli",), "other"),
        (("cli", "db"), "itni"),
    }


def test_unknown_key_hints_at_unique_parameter_owner():
    @click.group()
    def cli():
        pass

    @cli.command()
    @click.option("--port", type=int)
    def serve(port):
        pass

    issues = click.check_command(cli, default_map={"port": 5000}, file=io.StringIO())

    assert len(issues) == 1
    assert issues[0].key == "port"
    assert issues[0].command_path == ("cli",)
    assert issues[0].hint_path == ("cli", "serve")
    assert "cli serve" in issues[0].message


def test_unknown_key_hint_omitted_when_owners_ambiguous():
    @click.group()
    def cli():
        pass

    @cli.command()
    @click.option("--port", type=int)
    def a(port):
        pass

    @cli.command()
    @click.option("--port", type=int)
    def b(port):
        pass

    issues = click.check_command(cli, default_map={"port": 1}, file=io.StringIO())

    assert len(issues) == 1
    assert issues[0].hint_path is None


def test_no_fuzzy_suggestions():
    """A similar-but-different name must never be guessed as the owner."""
    cli, _ = build_tree()

    # 'verbose' exists at 'cli db init'; a typo and a prefix do not match.
    issues = click.check_command(
        cli, default_map={"db": {"init": {"verbose2": True}}}, file=io.StringIO()
    )
    assert len(issues) == 1
    assert issues[0].hint_path is None

    @click.command()
    @click.option("--username")
    def cmd(username):
        pass

    issues = click.check_command(cmd, default_map={"user": "x"}, file=io.StringIO())
    assert issues[0].hint_path is None


# ---------------------------------------------------------------------------
# Misplaced nested tables
# ---------------------------------------------------------------------------


def test_misplaced_nested_table_points_to_owner():
    cli, _ = build_tree()

    # 'init' is a subcommand of 'db', not of the root.
    issues = click.check_command(
        cli, default_map={"init": {"verbose": True}}, file=io.StringIO()
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.kind is MISPLACED_TABLE
    assert issue.key == "init"
    assert issue.command_path == ("cli",)
    assert issue.hint_path == ("cli", "db", "init")
    assert "cli db init" in issue.message


def test_correctly_nested_table_is_clean():
    cli, _ = build_tree()

    assert (
        click.check_command(cli, default_map={"db": {"init": {"verbose": True}}}) == []
    )


def test_misplaced_table_without_fitting_command_has_no_hint():
    cli, _ = build_tree()

    # A command named 'init' exists, but its keys do not fit the table.
    issues = click.check_command(
        cli, default_map={"init": {"unknown_param": 1}}, file=io.StringIO()
    )

    assert len(issues) == 1
    assert issues[0].kind is MISPLACED_TABLE
    assert issues[0].hint_path is None


def test_deeply_misplaced_table():
    @click.group()
    def cli():
        pass

    @cli.group()
    def a():
        pass

    @a.group()
    def b():
        pass

    @b.command()
    @click.option("--opt")
    def c(opt):
        pass

    issues = click.check_command(
        cli, default_map={"b": {"c": {"opt": 1}}}, file=io.StringIO()
    )
    assert len(issues) == 1
    assert issues[0].key == "b"
    assert issues[0].hint_path == ("cli", "a", "b")


def test_nested_table_on_plain_command_is_misplaced():
    @click.command()
    @click.option("--opt")
    def cli(opt):
        pass

    issues = click.check_command(
        cli, default_map={"sub": {"opt": 1}}, file=io.StringIO()
    )
    assert len(issues) == 1
    assert issues[0].kind is MISPLACED_TABLE


# ---------------------------------------------------------------------------
# context_settings
# ---------------------------------------------------------------------------


def test_unknown_context_setting_names_command_and_key():
    cmd = click.Command("plain", context_settings={"colr": True})

    issues = click.check_command(cmd, file=io.StringIO())

    assert len(issues) == 1
    issue = issues[0]
    assert issue.category is CONTEXT_SETTINGS
    assert issue.kind is UNKNOWN_KEY
    assert issue.key == "colr"
    assert issue.command_path == ("plain",)
    assert "plain" in issue.message
    assert "'colr'" in issue.message
    # Accepted keys are listed instead of leaving a bare TypeError.
    assert "color" in issue.message
    assert "terminal_width" in issue.message


def test_unknown_context_setting_in_nested_command_path():
    cli, _ = build_tree()
    init = cli.commands["db"].commands["init"]
    init.context_settings = {"hel_option_names": ["-h"]}

    issues = click.check_command(cli, file=io.StringIO())

    assert len(issues) == 1
    assert issues[0].key == "hel_option_names"
    assert issues[0].command_path == ("cli", "db", "init")


def test_reserved_context_keys_are_reported():
    # make_context() always supplies these itself; forwarding them via
    # context_settings raises a "multiple values" TypeError at runtime.
    for key in ("command", "parent", "info_name"):
        cmd = click.Command("plain", context_settings={key: "value"})
        issues = click.check_command(cmd, file=io.StringIO())
        assert len(issues) == 1, key
        assert issues[0].key == key


def test_accepted_context_keys_signature_introspection():
    from click.checking import accepted_context_keys

    class Explicit:
        def __init__(self, command, parent=None, info_name=None, custom_thing=None):
            pass

    accepted = accepted_context_keys(Explicit)

    assert accepted is not None
    assert "custom_thing" in accepted
    # Keys make_context() supplies itself can never come from settings.
    assert "command" not in accepted
    assert "parent" not in accepted
    assert "info_name" not in accepted
    assert "self" not in accepted
    assert "colr" not in accepted


def test_custom_context_class_extra_parameter_is_legal():
    class MyContext(click.Context):
        def __init__(self, *args, custom_thing: object = None, **kwargs):
            super().__init__(*args, **kwargs)
            self.custom_thing = custom_thing

    cmd = click.Command(
        "plain",
        context_settings={"custom_thing": 42, "colr": True},
    )
    cmd.context_class = MyContext

    # The subclass takes ``**kwargs``, so keys cannot be proven invalid and
    # the check must not risk a false positive.
    assert click.check_command(cmd) == []


def test_context_class_with_var_keyword_accepts_everything():
    class LooseContext(click.Context):
        def __init__(self, *args, **kwargs):
            kwargs.pop("anything_goes", None)
            super().__init__(*args)

    cmd = click.Command(
        "plain",
        context_settings={"anything_goes": 1},
    )
    cmd.context_class = LooseContext

    assert click.check_command(cmd) == []


# ---------------------------------------------------------------------------
# Dynamic trees and CommandCollection
# ---------------------------------------------------------------------------


def test_check_sees_tree_shape_at_call_time():
    cli = click.Group("cli")
    serve = click.Command("serve", params=[click.Option(["--port"], type=int)])

    # Before registration the nested table has nowhere to live.
    issues = click.check_command(
        cli, default_map={"serve": {"port": 5000}}, file=io.StringIO()
    )
    assert len(issues) == 1
    assert issues[0].kind is MISPLACED_TABLE

    # After dynamic registration the same map is valid.
    cli.add_command(serve)
    assert click.check_command(cli, default_map={"serve": {"port": 5000}}) == []


def test_check_sees_lazy_group_commands():
    class LazyGroup(click.Group):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._late_commands: dict[str, click.Command] = {}

        def list_commands(self, ctx):
            return sorted({*super().list_commands(ctx), *self._late_commands})

        def get_command(self, ctx, name):
            return self._late_commands.get(name) or super().get_command(ctx, name)

    cli = LazyGroup("cli")
    cli._late_commands["serve"] = click.Command(
        "serve", params=[click.Option(["--port"], type=int)]
    )

    assert click.check_command(cli, default_map={"serve": {"port": 5000}}) == []
    issues = click.check_command(
        cli, default_map={"serve": {"prt": 5000}}, file=io.StringIO()
    )
    assert issues[0].command_path == ("cli", "serve")


def test_command_collection_flattens_sources():
    source = click.Group(
        "source",
        commands={
            "serve": click.Command("serve", params=[click.Option(["--port"], type=int)])
        },
    )
    collection = click.CommandCollection("cli", sources=[source])

    # The subcommand is reached directly on the collection, not under the
    # source group's name.
    assert click.check_command(collection, default_map={"serve": {"port": 5000}}) == []

    # Nesting under the source name is never consumed.
    issues = click.check_command(
        collection,
        default_map={"source": {"serve": {"port": 5000}}},
        file=io.StringIO(),
    )
    assert len(issues) == 1
    assert issues[0].key == "source"
    assert issues[0].kind is MISPLACED_TABLE


# ---------------------------------------------------------------------------
# Where the default map comes from
# ---------------------------------------------------------------------------


def test_root_context_settings_default_map_is_used_by_default():
    cmd = click.Command(
        "plain",
        context_settings={"default_map": {"nme": "x"}},
        params=[click.Option(["--name"])],
    )

    issues = cmd.check()

    assert len(issues) == 1
    assert issues[0].key == "nme"


def test_explicit_default_map_argument_wins_over_context_settings():
    cmd = click.Command(
        "plain",
        context_settings={"default_map": {"name": "from-settings"}},
        params=[click.Option(["--name"])],
    )

    # Explicit valid map: the bad settings map is not consulted.
    assert click.check_command(cmd, default_map={"name": "arg"}) == []

    issues = click.check_command(cmd, default_map={"nme": "x"}, file=io.StringIO())
    assert issues[0].key == "nme"


def test_child_context_settings_default_map_replaces_inherited_table():
    cli, init = build_tree()

    # The child declares its own map; the nested 'init' table the parent
    # would provide is ignored at runtime, so its keys are not checked here.
    init.context_settings = {"default_map": {"verbose": True}}

    assert (
        click.check_command(cli, default_map={"db": {"init": {"verbos": True}}}) == []
    )

    # The child's own declared map IS checked.
    init.context_settings = {"default_map": {"verbos": True}}
    issues = click.check_command(cli, file=io.StringIO())
    assert len(issues) == 1
    assert issues[0].key == "verbos"
    assert issues[0].command_path == ("cli", "db", "init")


# ---------------------------------------------------------------------------
# Two modes: advisory print vs strict exception
# ---------------------------------------------------------------------------


def test_advisory_mode_writes_to_stderr(capsys):
    cli, _ = build_tree()

    issues = click.check_command(cli, default_map={"debig": True})

    assert len(issues) == 1
    captured = capsys.readouterr()
    assert "debig" in captured.err
    assert captured.out == ""


def test_advisory_mode_writes_to_given_file(capsys):
    cli, _ = build_tree()
    stream = io.StringIO()

    issues = click.check_command(cli, default_map={"debig": True}, file=stream)

    assert len(issues) == 1
    assert "debig" in stream.getvalue()
    assert capsys.readouterr().err == ""


def test_strict_mode_raises_with_full_report_and_prints_nothing(capsys):
    cli, _ = build_tree()

    with pytest.raises(click.ConfigCheckError) as exc_info:
        click.check_command(
            cli,
            default_map={"debig": True, "db": {"itni": {"verbose": True}}},
            strict=True,
        )

    error = exc_info.value
    assert len(error.issues) == 2
    assert "debig" in str(error)
    assert "itni" in str(error)
    assert capsys.readouterr().err == ""


def test_strict_mode_clean_does_not_raise(capsys):
    cli, _ = build_tree()

    issues = click.check_command(
        cli, default_map={"db": {"init": {"verbose": True}}}, strict=True
    )

    assert issues == []
    assert capsys.readouterr().err == ""


def test_command_check_method_matches_function(capsys):
    cli, _ = build_tree()

    assert cli.check(default_map={"debig": True}) == click.check_command(
        cli, default_map={"debig": True}, file=io.StringIO()
    )
    capsys.readouterr()


# ---------------------------------------------------------------------------
# The runtime path is untouched
# ---------------------------------------------------------------------------


def test_runtime_behavior_and_precedence_unchanged(runner):
    cli, _ = build_tree()

    result = runner.invoke(
        cli,
        ["db", "init"],
        default_map={"db": {"init": {"verbose": True}}},
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "verbose=True"

    result = runner.invoke(cli, ["db", "init", "--verbose"])
    assert result.exit_code == 0
    assert result.output.strip() == "verbose=True"


def test_check_does_not_mutate_command_or_break_help(runner):
    cli, _ = build_tree()

    click.check_command(cli, default_map={"db": {"init": {"verbose": True}}})

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--debug" in result.output

    result = runner.invoke(cli, ["db", "init", "--help"])
    assert result.exit_code == 0
    assert "--verbose" in result.output


def test_unknown_context_setting_still_type_errors_at_runtime():
    # The check is an independent entry point; make_context keeps forwarding
    # context_settings verbatim.
    cmd = click.Command("plain", context_settings={"colr": True})

    with pytest.raises(TypeError, match="colr"):
        cmd.make_context("plain", [])


def test_bare_type_error_message_has_no_command_owner():
    """Documents the pre-check pain: a bare TypeError names only the key."""
    cmd = click.Command("plain", context_settings={"colr": True})

    with pytest.raises(TypeError) as exc_info:
        cmd.make_context("plain", [])

    assert "plain" not in str(exc_info.value)
