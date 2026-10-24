"""Tests for the ``sensitive`` parameter marker.

A sensitive parameter keeps working exactly as before for valid input: its
value is converted and passed to callbacks and the command unchanged. What
changes is that the value never appears in Click-rendered error messages or
in help's default display; a fixed mask is shown instead.
"""

import pytest

import click
from click._utils import UNSET

SECRET = "secrettoken"
MASK = "'***'"


# ---------------------------------------------------------------------------
# Conversion errors
# ---------------------------------------------------------------------------


def test_sensitive_option_masks_conversion_error(runner):
    @click.command()
    @click.option("--token", type=int, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 2
    assert "Invalid value for '--token'" in result.output
    assert MASK in result.output
    assert SECRET not in result.output


def test_sensitive_argument_masks_conversion_error(runner):
    @click.command()
    @click.argument("token", type=int, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, [SECRET])
    assert result.exit_code == 2
    assert "Invalid value for 'TOKEN'" in result.output
    assert MASK in result.output
    assert SECRET not in result.output


def test_unsensitive_option_error_is_verbatim(runner):
    @click.command()
    @click.option("--token", type=int)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 2
    assert (
        f"Invalid value for '--token': '{SECRET}' is not a valid integer."
        in result.output
    )
    assert MASK not in result.output


@pytest.mark.parametrize(
    ("param_type", "args", "expected"),
    [
        pytest.param(
            click.Choice(["a", "b"]),
            [SECRET],
            f"{MASK} is not one of 'a', 'b'.",
            id="choice",
        ),
        pytest.param(click.UUID, [SECRET], f"{MASK} is not a valid UUID.", id="uuid"),
        pytest.param(
            click.DateTime(),
            [SECRET],
            f"{MASK} does not match the format",
            id="datetime",
        ),
        pytest.param(
            click.BOOL,
            [SECRET],
            f"{MASK} is not a valid boolean. Recognized values:",
            id="bool",
        ),
        pytest.param(
            click.IntRange(0, 10),
            ["200"],
            "*** is not in the range 0<=x<=10.",
            id="range-unquoted-mask",
        ),
    ],
)
def test_sensitive_builtin_types_mask_value(runner, param_type, args, expected):
    @click.command()
    @click.option("--token", type=param_type, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", *args])
    assert result.exit_code == 2
    assert expected in result.output
    assert args[0] not in result.output


def test_sensitive_mask_has_fixed_shape(runner):
    """The mask never varies with the length or shape of the value."""

    @click.command()
    @click.option("--token", type=int, sensitive=True)
    def cli(token):
        click.echo(token)

    short = runner.invoke(cli, ["--token", "a"])
    long = runner.invoke(cli, ["--token", "a-very-long-secret-value"])
    assert short.output.count(MASK) == 1
    assert long.output.count(MASK) == 1
    assert "a-very-long-secret-value" not in long.output


def test_sensitive_path_masks_value(runner, tmp_path):
    missing = tmp_path / SECRET

    @click.command()
    @click.option("--path", type=click.Path(exists=True), sensitive=True)
    def cli(path):
        click.echo(path)

    result = runner.invoke(cli, ["--path", str(missing)])
    assert result.exit_code == 2
    assert f"Path {MASK} does not exist." in result.output
    assert SECRET not in result.output


def test_sensitive_file_masks_open_error(runner, tmp_path):
    @click.command()
    @click.option("--file", type=click.File("w", lazy=False), sensitive=True)
    def cli(file):
        click.echo(file)

    # A directory cannot be opened for writing.
    result = runner.invoke(cli, ["--file", str(tmp_path)])
    assert result.exit_code == 2
    assert "Invalid value for '--file'" in result.output
    assert str(tmp_path) not in result.output


# ---------------------------------------------------------------------------
# Multi values and tuples: each element is masked separately
# ---------------------------------------------------------------------------


def test_sensitive_nargs_masks_each_element(runner):
    @click.command()
    @click.option("--pair", nargs=2, type=int, sensitive=True)
    def cli(pair):
        click.echo(pair)

    result = runner.invoke(cli, ["--pair", "1", SECRET])
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output
    # The valid sibling value is not echoed in the error either.
    assert "Invalid value for '--pair'" in result.output


def test_sensitive_tuple_type_masks_each_element(runner):
    @click.command()
    @click.option("--pair", type=(int, int), sensitive=True)
    def cli(pair):
        click.echo(pair)

    result = runner.invoke(cli, ["--pair", SECRET, "2"])
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


def test_sensitive_multiple_masks_each_occurrence(runner):
    @click.command()
    @click.option("--token", type=int, multiple=True, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", "1", "--token", SECRET, "--token", "3"])
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


def test_sensitive_multiple_from_envvar_masks_each_element(runner):
    @click.command()
    @click.option(
        "--token",
        type=int,
        multiple=True,
        envvar="TOKENS",
        sensitive=True,
    )
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, [], env={"TOKENS": f"1 {SECRET} 3"})
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


# ---------------------------------------------------------------------------
# Value sources: command line, environment variable, default map
# ---------------------------------------------------------------------------


def test_sensitive_envvar_value_is_masked(runner):
    @click.command()
    @click.option("--token", envvar="TOKEN", type=int, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, [], env={"TOKEN": SECRET})
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


def test_sensitive_default_map_value_is_masked(runner):
    @click.command()
    @click.option("--token", type=int, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, [], default_map={"token": SECRET})
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


def test_sensitive_default_value_is_converted_and_masked_in_error(runner):
    @click.command()
    @click.option("--token", type=int, default=SECRET, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli)
    assert result.exit_code == 2
    assert MASK in result.output
    assert SECRET not in result.output


# ---------------------------------------------------------------------------
# Valid input and callbacks are unaffected
# ---------------------------------------------------------------------------


def test_sensitive_valid_value_is_passed_through(runner):
    seen = {}

    def callback(ctx, param, value):
        seen["value"] = value
        return value

    @click.command()
    @click.option("--token", callback=callback, sensitive=True)
    def cli(token):
        click.echo(f"got {token}")

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 0
    assert seen["value"] == SECRET
    assert f"got {SECRET}" in result.output


def test_sensitive_callback_error_is_masked(runner):
    def callback(ctx, param, value):
        if value == SECRET:
            raise click.BadParameter(f"token {value!r} rejected")

        return value

    @click.command()
    @click.option("--token", callback=callback, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 2
    assert "Invalid value for '--token': token '***' rejected" in result.output
    assert SECRET not in result.output


def test_sensitive_custom_type_message_is_masked(runner):
    class CustomType(click.ParamType):
        name = "custom"

        def convert(self, value, param, ctx):
            self.fail(f"rejected: {value}", param, ctx)

    @click.command()
    @click.option("--token", type=CustomType(), sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 2
    assert "rejected: '***'" in result.output
    assert SECRET not in result.output


def test_sensitive_function_type_empty_error_is_masked(runner):
    def convert(value):
        if value == SECRET:
            raise ValueError("")

        return value

    @click.command()
    @click.option("--token", type=convert, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli, ["--token", SECRET])
    assert result.exit_code == 2
    assert f"Invalid value for '--token': {MASK}" in result.output
    assert SECRET not in result.output


def test_other_parameter_error_is_not_masked(runner):
    """Masking follows the parameter that raised the error; an error raised
    by a non-sensitive parameter keeps its verbatim value even when another
    parameter on the same command is sensitive.
    """

    @click.command()
    @click.option("--token", sensitive=True)
    @click.option("--n", type=int)
    def cli(token, n):
        click.echo(f"{token}|{n}")

    result = runner.invoke(cli, ["--token", SECRET, "--n", "bad"])
    assert result.exit_code == 2
    assert "Invalid value for '--n': 'bad' is not a valid integer." in result.output
    assert MASK not in result.output


# ---------------------------------------------------------------------------
# Missing and usage errors still name the parameter, never the value
# ---------------------------------------------------------------------------


def test_sensitive_required_missing_error_is_unchanged(runner):
    @click.command()
    @click.option("--token", required=True, sensitive=True)
    def cli(token):
        click.echo(token)

    result = runner.invoke(cli)
    assert result.exit_code == 2
    assert "Missing option '--token'." in result.output
    assert MASK not in result.output


def test_sensitive_parser_usage_error_does_not_leak_value(runner):
    @click.command()
    @click.option("--token", sensitive=True)
    def cli(token):
        click.echo(token)

    # The option is given but its value is missing; the parser only knows
    # the option name, so the message is the regular usage error.
    result = runner.invoke(cli, ["--token"])
    assert result.exit_code == 2
    assert "requires an argument" in result.output
    assert "'--token'" in result.output


# ---------------------------------------------------------------------------
# Help: the default of a sensitive parameter is not displayed
# ---------------------------------------------------------------------------


def test_sensitive_default_not_in_help(runner):
    @click.command()
    @click.option("--token", default=SECRET, show_default=True, sensitive=True)
    @click.option("--plain", default="hello", show_default=True)
    def cli(token, plain):
        pass

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert SECRET not in result.output
    assert "default:" not in result.output.split("--token")[1].split("\n")[0]
    assert "[default: hello]" in result.output


def test_sensitive_default_not_in_help_with_context_show_default():
    @click.command()
    @click.option("--token", default=SECRET, sensitive=True)
    def cli(token):
        pass

    # Even when the context turns default display on globally, the
    # sensitive parameter's default stays hidden.
    ctx = click.Context(cli, info_name="cli", show_default=True)
    extra = cli.params[0].get_help_extra(ctx)
    assert "default" not in extra


def test_sensitive_custom_show_default_string_not_in_help(runner):
    @click.command()
    @click.option(
        "--token",
        default=SECRET,
        show_default="(dynamic)",
        sensitive=True,
    )
    def cli(token):
        pass

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "(dynamic)" not in result.output
    assert SECRET not in result.output


# ---------------------------------------------------------------------------
# Declaration time validation and the relationship with ``hide_input``
# ---------------------------------------------------------------------------


def test_sensitive_argument_is_accepted():
    param = click.Argument(["token"], sensitive=True)
    assert param.sensitive is True


def test_sensitive_implies_hide_input():
    param = click.Option(["--token"], sensitive=True)
    assert param.sensitive is True
    assert param.hide_input is True


def test_hide_input_alone_does_not_set_sensitive():
    param = click.Option(["--token"], hide_input=True)
    assert param.hide_input is True
    assert param.sensitive is False


def test_sensitive_with_explicit_hide_input_true_is_accepted():
    param = click.Option(["--token"], sensitive=True, hide_input=True)
    assert param.hide_input is True


def test_sensitive_with_hide_input_false_raises():
    with pytest.raises(TypeError, match="'sensitive' implies 'hide_input'"):
        click.Option(["--token"], sensitive=True, hide_input=False)


def test_sensitive_with_bool_flag_raises():
    with pytest.raises(TypeError, match="'sensitive' is not valid for boolean flag"):
        click.Option(["--token"], is_flag=True, sensitive=True)

    with pytest.raises(TypeError, match="'sensitive' is not valid for boolean flag"):
        click.Option(["--token/--no-token"], sensitive=True)


def test_sensitive_non_bool_flag_is_accepted():
    param = click.Option(["--token"], is_flag=True, flag_value="static", sensitive=True)
    assert param.sensitive is True


# ---------------------------------------------------------------------------
# Prompt path keeps the hide_input behavior
# ---------------------------------------------------------------------------


def test_sensitive_prompt_hides_input_and_masks_retry(runner):
    @click.command()
    @click.option("--token", prompt=True, type=int, sensitive=True)
    def cli(token):
        click.echo(f"got {token}")

    result = runner.invoke(cli, input=f"{SECRET}\n42\n")
    assert result.exit_code == 0
    assert f"Error: {MASK} is not a valid integer." in result.output
    assert SECRET not in result.output
    assert "got 42" in result.output


# ---------------------------------------------------------------------------
# resilient_parsing keeps tolerating bad values
# ---------------------------------------------------------------------------


def test_sensitive_resilient_parsing_tolerates_bad_default(runner):
    @click.command()
    @click.option("--token", default=SECRET, type=int, sensitive=True)
    def cli(token):
        return token

    # Help rendering forces resilient parsing internally; the invalid
    # default must neither abort help nor leak into the output.
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert SECRET not in result.output


def test_sensitive_resilient_parsing_tolerates_bad_value(runner):
    seen = []

    @click.command()
    @click.option("--token", type=int, sensitive=True)
    def cli(token):
        seen.append(token)

    ctx = click.Context(cli, resilient_parsing=True)
    param = cli.params[0]
    # Conversion errors are swallowed by the parameter processing layer in
    # resilient mode, exactly as for a non-sensitive parameter.
    value, _ = param.handle_parse_result(ctx, {"token": SECRET}, [])
    assert value is UNSET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_choice_invalid_message_masks_for_sensitive_param():
    param = click.Option(["--token"], sensitive=True)
    message = click.Choice(["a", "b"]).get_invalid_choice_message(
        SECRET, ctx=None, param=param
    )
    assert message == f"{MASK} is not one of 'a', 'b'."


def test_choice_invalid_message_unchanged_without_param():
    message = click.Choice(["a", "b"]).get_invalid_choice_message("c", ctx=None)
    assert message == "'c' is not one of 'a', 'b'."
