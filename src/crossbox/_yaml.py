"""The one YAML loader every crossbox reader uses.

``yaml.safe_load`` plus three refusals:

- a DUPLICATE mapping key, at any depth. PyYAML keeps the last value, so a
  task holding two ``steps:`` blocks parsed as the second one and the next
  write dropped the first. A repeated key is ambiguous input, and ambiguous
  input is refused (a task writer exits 1 under invariant 6; claims,
  config and registry are NOT EVALUABLE), never quietly resolved.
- a document over ``MAX_BYTES``. Queue, claims, config and registry files are
  small; one far past the budget is not a task file.
- more than ``MAX_ALIASES`` alias references. SafeLoader shares an aliased
  node rather than copying it, so a nested "billion laughs" document parses
  cheaply, but the canonical writer never emits aliases (model._Dumper) and
  expands every shared node on the next write. crossbox writes no aliases
  itself, so a small cap costs nothing: 16 references bound the expansion
  near 2^8 copies.

Duplicate detection follows the pattern of a strict loader written for a
sibling project; the error carries a mark, so callers report the line.
"""
from __future__ import annotations

import yaml

MAX_BYTES = 8 * 1024 * 1024
MAX_ALIASES = 16


class DuplicateKeyError(yaml.MarkedYAMLError):
    pass


class BudgetError(yaml.YAMLError):
    pass


class UnbuildableValueError(yaml.YAMLError):
    pass


class _StrictLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._aliases = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            self._aliases += 1
            if self._aliases > MAX_ALIASES:
                raise BudgetError(f"more than {MAX_ALIASES} alias references")
        return super().compose_node(parent, index)


def _construct_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    seen = set()
    for key_node, _value in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            dup = key in seen
        except TypeError:               # unhashable key: PyYAML reports it
            continue
        if dup:
            raise DuplicateKeyError(
                problem=f"duplicate key {key!r}",
                problem_mark=key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_strict(text: str):
    """``yaml.safe_load(text)`` that raises on a duplicate key or a blown budget.
    Every error is a ``yaml.YAMLError``, so existing handlers catch it."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        raise BudgetError(f"document is over {MAX_BYTES} bytes")
    # _StrictLoader subclasses SafeLoader and only adds refusals.
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506  # nosec B506
    except (ValueError, RecursionError) as e:
        # PyYAML raises these outside its own error tree: an impossible date
        # ("2026-02-30") and nesting deeper than the interpreter's stack.
        what = "nesting too deep" if isinstance(e, RecursionError) else str(e)
        raise UnbuildableValueError(f"a value cannot be read: {what}") from None
