"""Explicit validation of ``default_map`` and ``context_settings`` keys.

The runtime is deliberately permissive: a ``default_map`` key that no
parameter or subcommand consumes is silently ignored, and an unknown
``context_settings`` key is forwarded into the context constructor where it
surfaces as a bare :class:`TypeError` without saying which command it belongs
to. This module provides an independent check entry point that walks the final
shape of a command tree and reports every such key at once, with the command
path and key name.

The check never participates in parsing or context construction. Values in a
``default_map`` are not inspected, only its keys, and precedence or
inheritance rules are not changed.

.. versionadded:: 8.6.0
"""

from __future__ import annotations

import collections.abc as cabc
import inspect
import sys
import typing as t

from ._utils import UNSET
from .core import Command
from .core import Context
from .core import Group
from .exceptions import ClickException
from .utils import echo

#: The key is not accepted by the command's context class constructor.
CONTEXT_SETTINGS = "context_settings"
#: The key does not match a parameter or a subcommand of the command.
DEFAULT_MAP = "default_map"

#: A ``context_settings`` key the context constructor does not accept.
UNKNOWN_KEY = "unknown_key"
#: A nested table keyed by a name that is not a subcommand at that level.
MISPLACED_TABLE = "misplaced_table"

#: Names passed explicitly by :meth:`Command.make_context`, so they can never
#: be supplied through ``context_settings`` without colliding with the call.
_RESERVED_CONTEXT_KEYS = frozenset({"self", "command", "parent", "info_name"})


class ConfigIssue:
    """A single problem found in a command's configuration.

    :param command_path: Names from the root command to the command the issue
        belongs to. An empty tuple denotes the root command when it has no name.
    :param category: :data:`CONTEXT_SETTINGS` or :data:`DEFAULT_MAP`.
    :param kind: :data:`UNKNOWN_KEY` or :data:`MISPLACED_TABLE`.
    :param key: The offending key in the mapping.
    :param message: A complete, standalone description of the issue.
    :param hint_path: Command path an entry was likely intended for, if a
        unique deterministic candidate exists.

    .. versionadded:: 8.6.0
    """

    def __init__(
        self,
        command_path: tuple[str, ...],
        category: str,
        kind: str,
        key: str,
        message: str,
        hint_path: tuple[str, ...] | None = None,
    ) -> None:
        self.command_path = command_path
        self.category = category
        self.kind = kind
        self.key = key
        self.message = message
        self.hint_path = hint_path

    @property
    def command_name(self) -> str:
        """The command path rendered as a space-separated string."""
        return render_command_path(self.command_path)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return (
            f"<ConfigIssue category={self.category!r} kind={self.kind!r}"
            f" command={self.command_name!r} key={self.key!r}>"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConfigIssue):
            return NotImplemented

        return (
            self.command_path == other.command_path
            and self.category == other.category
            and self.kind == other.kind
            and self.key == other.key
            and self.hint_path == other.hint_path
        )

    def __hash__(self) -> int:
        return hash(
            (self.command_path, self.category, self.kind, self.key, self.hint_path)
        )


class ConfigCheckError(ClickException):
    """Raised by the strict mode of :func:`check_command`.

    :attr:`issues` holds the complete, unsummarized report so callers can
    inspect it programmatically.

    .. versionadded:: 8.6.0
    """

    issues: t.Sequence[ConfigIssue]

    def __init__(self, issues: cabc.Sequence[ConfigIssue]) -> None:
        self.issues = list(issues)
        super().__init__(format_report(self.issues))


def render_command_path(path: cabc.Sequence[str]) -> str:
    """Render a command path tuple for messages."""
    return " ".join(path) if path else "(root)"


def format_report(issues: cabc.Sequence[ConfigIssue]) -> str:
    """Format all issues into a single human-readable report."""
    count = len(issues)
    lines = [f"Configuration check found {count} issue{'s' if count != 1 else ''}:"]
    lines.extend(f"  - {issue.message}" for issue in issues)
    return "\n".join(lines)


def accepted_context_keys(context_class: type[Context]) -> frozenset[str] | None:
    """Return the keyword names ``context_settings`` may use for the class.

    Returns ``None`` when the constructor accepts arbitrary keyword arguments
    (``**kwargs``), in which case no key can be rejected. Names always supplied
    by :meth:`Command.make_context` (``command``, ``parent``, ``info_name``)
    are excluded: passing them through ``context_settings`` raises a
    "multiple values" error instead of reaching the constructor normally.
    """
    try:
        parameters = inspect.signature(context_class.__init__).parameters
    except (TypeError, ValueError):
        return None

    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return None

    return frozenset(
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and name not in _RESERVED_CONTEXT_KEYS
    )


class _Node(t.NamedTuple):
    command: Command
    context: Context
    path: tuple[str, ...]
    default_map: cabc.MutableMapping[str, t.Any] | None
    param_names: frozenset[str]
    subcommand_names: frozenset[str]


class _Collector:
    def __init__(self, command: Command, root_default_map: t.Any) -> None:
        self._root = command
        self._root_default_map = root_default_map
        self.nodes: list[_Node] = []
        # Parameter name -> command paths that define it.
        self._params_by_name: dict[str, list[tuple[str, ...]]] = {}
        # Subcommand name -> nodes that expose it.
        self._nodes_by_command_name: dict[str, list[_Node]] = {}
        self._accepted_cache: dict[type[Context], frozenset[str] | None] = {}

    def collect(self) -> list[ConfigIssue]:
        root_map = (
            self._root_default_map
            if isinstance(self._root_default_map, cabc.MutableMapping)
            else None
        )
        root_path = (self._root.name,) if self._root.name is not None else ()
        root_ctx = self._root.context_class(
            self._root,
            info_name=self._root.name,
            default_map=root_map,
        )

        with root_ctx.scope(cleanup=False):
            self._visit(self._root, root_ctx, root_path, root_map, frozenset())

        return self._build_issues()

    def _visit(
        self,
        command: Command,
        ctx: Context,
        path: tuple[str, ...],
        default_map: cabc.MutableMapping[str, t.Any] | None,
        ancestors: frozenset[int],
    ) -> None:
        param_names = frozenset(param.name for param in command.get_params(ctx))
        subcommand_names: frozenset[str] = frozenset()

        if isinstance(command, Group):
            subcommand_names = frozenset(command.list_commands(ctx))

        node = _Node(
            command=command,
            context=ctx,
            path=path,
            default_map=default_map,
            param_names=param_names,
            subcommand_names=subcommand_names,
        )
        self.nodes.append(node)

        for name in param_names:
            self._params_by_name.setdefault(name, []).append(path)

        if path:
            self._nodes_by_command_name.setdefault(path[-1], []).append(node)

        if not isinstance(command, Group):
            return

        next_ancestors = ancestors | {id(command)}

        for name in command.list_commands(ctx):
            subcommand = command.get_command(ctx, name)

            if subcommand is None or id(subcommand) in next_ancestors:
                continue

            sub_map = self._resolve_default_map(subcommand, name, default_map)
            sub_map = sub_map if isinstance(sub_map, cabc.MutableMapping) else None
            sub_ctx = subcommand.context_class(
                subcommand,
                info_name=name,
                parent=ctx,
                default_map=sub_map,
            )

            with sub_ctx.scope(cleanup=False):
                self._visit(
                    subcommand,
                    sub_ctx,
                    (*path, name),
                    sub_map,
                    next_ancestors,
                )

    @staticmethod
    def _resolve_default_map(
        command: Command,
        info_name: str,
        parent_map: cabc.MutableMapping[str, t.Any] | None,
    ) -> t.Any:
        # A command's own ``context_settings`` injects ``default_map`` as a
        # constructor keyword, which wins over the inherited nested table. This
        # mirrors ``Context.__init__`` exactly.
        explicit = command.context_settings.get("default_map", UNSET)

        if explicit is not UNSET:
            return explicit

        if parent_map is not None:
            return parent_map.get(info_name)

        return None

    def _accepted_keys(self, command: Command) -> frozenset[str] | None:
        context_class = command.context_class

        if context_class not in self._accepted_cache:
            self._accepted_cache[context_class] = accepted_context_keys(context_class)

        return self._accepted_cache[context_class]

    def _build_issues(self) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []

        for node in self.nodes:
            issues.extend(self._context_settings_issues(node))
            issues.extend(self._default_map_issues(node))

        issues.sort(key=lambda issue: (issue.command_path, issue.category, issue.key))
        return issues

    def _context_settings_issues(self, node: _Node) -> cabc.Iterator[ConfigIssue]:
        accepted = self._accepted_keys(node.command)

        if accepted is None:
            return

        for key in node.command.context_settings:
            if key in accepted:
                continue

            accepted_str = ", ".join(sorted(accepted))
            message = (
                f"command {render_command_path(node.path)!r}: unknown"
                f" context_settings key {key!r}; it would raise a TypeError when"
                f" the context is created. Accepted keys: {accepted_str}"
            )
            yield ConfigIssue(node.path, CONTEXT_SETTINGS, UNKNOWN_KEY, key, message)

    def _default_map_issues(self, node: _Node) -> cabc.Iterator[ConfigIssue]:
        if node.default_map is None:
            return

        for key, value in node.default_map.items():
            if key in node.param_names:
                # A parameter consumes this entry. Its value is never
                # inspected, even when it happens to be a mapping.
                continue

            if key in node.subcommand_names:
                # A subcommand inherits this entry as its own default map.
                # Descending was handled during traversal.
                continue

            if isinstance(value, cabc.MutableMapping):
                yield self._misplaced_table_issue(node, key, value)
            else:
                yield self._unknown_default_map_key_issue(node, key)

    def _unknown_default_map_key_issue(self, node: _Node, key: str) -> ConfigIssue:
        message = (
            f"command {render_command_path(node.path)!r}: default_map key"
            f" {key!r} is not consumed by any parameter or subcommand"
        )
        hint_path = self._parameter_owner_hint(node, key)

        if hint_path is not None:
            message += (
                f"; a parameter with this name is defined on command"
                f" {render_command_path(hint_path)!r}"
            )

        return ConfigIssue(node.path, DEFAULT_MAP, UNKNOWN_KEY, key, message, hint_path)

    def _misplaced_table_issue(
        self, node: _Node, key: str, table: cabc.MutableMapping[str, t.Any]
    ) -> ConfigIssue:
        message = (
            f"command {render_command_path(node.path)!r}: default_map key"
            f" {key!r} holds a nested table, but {key!r} is not a subcommand"
            " at this level"
        )
        hint_path = self._table_owner_hint(node, key, table)

        if hint_path is not None:
            message += (
                f"; a command named {key!r} exists at"
                f" {render_command_path(hint_path)!r}"
            )

        return ConfigIssue(
            node.path, DEFAULT_MAP, MISPLACED_TABLE, key, message, hint_path
        )

    def _parameter_owner_hint(self, node: _Node, key: str) -> tuple[str, ...] | None:
        owners = [
            path for path in self._params_by_name.get(key, ()) if path != node.path
        ]
        # Only attribute when the match is unique; never guess on similar
        # names.
        if len(set(owners)) == 1:
            return owners[0]

        return None

    def _table_owner_hint(
        self,
        node: _Node,
        key: str,
        table: cabc.MutableMapping[str, t.Any],
    ) -> tuple[str, ...] | None:
        inner_keys = frozenset(table)

        # Preferred evidence: a command registered under exactly this name
        # (the inheritance key must equal the subcommand name), preferably
        # one whose parameters and subcommands accept every inner key.
        candidates = [
            candidate
            for candidate in self._nodes_by_command_name.get(key, ())
            if candidate.path != node.path
        ]

        if candidates:
            return self._best_candidate(candidates, inner_keys)

        # Fallback evidence: the nested keys all belong to one other command,
        # e.g. the table was both moved to the wrong level and renamed.
        if inner_keys:
            covering = [
                candidate
                for candidate in self.nodes
                if candidate.path != node.path
                and inner_keys <= candidate.param_names | candidate.subcommand_names
            ]

            if len({candidate.path for candidate in covering}) == 1:
                return covering[0].path

        return None

    @staticmethod
    def _best_candidate(
        candidates: cabc.Sequence[_Node], inner_keys: frozenset[str]
    ) -> tuple[str, ...] | None:
        def score(candidate: _Node) -> int:
            legal = candidate.param_names | candidate.subcommand_names
            return len(inner_keys & legal)

        ranked = sorted(candidates, key=score, reverse=True)
        best_score = score(ranked[0])

        if best_score == 0:
            # A same-name command exists elsewhere, but the table's keys do not
            # fit it; reporting a confident owner would be a guess.
            return None

        tied = [candidate for candidate in ranked if score(candidate) == best_score]
        paths = {candidate.path for candidate in tied}

        if len(paths) == 1:
            return tied[0].path

        return None


def check_command(
    command: Command,
    *,
    default_map: t.Any = UNSET,
    strict: bool = False,
    file: t.IO[str] | None = None,
) -> list[ConfigIssue]:
    """Check a command tree's ``default_map`` and ``context_settings`` keys.

    The tree is traversed in its current shape: commands registered
    dynamically and the flattened result of a :class:`CommandCollection` are
    seen exactly as they exist when this is called. For each command the legal
    key sets are:

    - the names of its parameters for ``default_map`` entries,
    - the names of its subcommands for nested ``default_map`` tables, and
    - the keyword parameter names accepted by its context class for
      ``context_settings`` entries.

    Every unconsumed ``default_map`` key, every nested table placed at the
    wrong level, and every unrecognized ``context_settings`` key is reported
    with the command path and the key name. Values in ``default_map`` are not
    validated, and parsing is never started.

    :param command: Root of the command tree to check.
    :param default_map: The default map the application passes to the root,
        equivalent to the ``default_map`` argument of :meth:`Command.main` or
        :class:`Context`. When omitted, the ``default_map`` entry of the root
        command's ``context_settings`` is used, if any.
    :param strict: Print the report to ``file`` (standard error by default)
        when ``False``; raise :class:`ConfigCheckError` with the same report
        when ``True``.
    :param file: Stream the advisory report is written to.
    :return: The complete list of issues in a stable order. An empty list
        means the configuration is valid and produces no output.

    .. versionadded:: 8.6.0
    """
    if default_map is UNSET:
        default_map = command.context_settings.get("default_map")

    issues = _Collector(command, default_map).collect()

    if not issues:
        return []

    if strict:
        raise ConfigCheckError(issues)

    echo(format_report(issues), file=file if file is not None else sys.stderr)
    return issues
