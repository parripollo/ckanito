# encoding: utf-8
"""Parser for the subset of the Lucene query syntax accepted by CKAN.

CKAN's ``package_search`` action takes ``q`` and ``fq`` written in Lucene
syntax and passes them straight to the search backend. This module turns
such strings into a small AST that the PostgreSQL backend compiles to SQL.

Supported::

    word  "a phrase"  wild*  wi?ld  *:*
    field:value  field:"phrase"  field:*  field:(a OR b)
    field:[a TO b]  field:{a TO b]  field:[* TO b]
    +required  -excluded  NOT a  !a
    a AND b  a && b  a OR b  a || b  (a OR b) AND c
    term^2 (boost, ignored)  term~ (fuzzy, ignored)
    two words                (implicit AND, as CKAN sets q.op=AND)

Not supported (a ``SearchQueryError`` is raised): local params ``{!...}``,
function queries and anything that does not tokenize.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

from ckan.lib.search.common import SearchQueryError

__all__ = ["parse", "unescape", "Term", "Range", "And", "Or", "Not",
           "MatchAll", "Node"]


@dataclass(frozen=True)
class Term:
    """``field:value``. ``field`` is None for the default field.

    ``phrase`` is True for quoted values. Unquoted values keep their ``*``
    and ``?`` wildcards; other escaped characters are already unescaped.
    """
    field: Optional[str]
    value: str
    phrase: bool = False


@dataclass(frozen=True)
class Range:
    field: Optional[str]
    low: Optional[str]      # None means open bound (``*``)
    high: Optional[str]
    include_low: bool = True
    include_high: bool = True


@dataclass(frozen=True)
class And:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Or:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Not:
    operand: "Node"


@dataclass(frozen=True)
class MatchAll:
    pass


Node = Union[Term, Range, And, Or, Not, MatchAll]


_TOKEN_RE = re.compile(r'''
    (?P<ws>\s+)
  | (?P<quoted>"(?:\\.|[^"\\])*")
  | (?P<lparen>\()
  | (?P<rparen>\))
  | (?P<lrange>[\[{])
  | (?P<rrange>[\]}])
  | (?P<colon>:)
  | (?P<boost>\^[0-9]*(?:\.[0-9]+)?)
  | (?P<fuzzy>~[0-9]*(?:\.[0-9]+)?)
  | (?P<op>&&|\|\||!)
  | (?P<plus>\+)
  | (?P<minus>-)
  | (?P<word>(?:\\.|[^\s()\[\]{}:"\\^~+\-!&|])(?:\\.|[^\s()\[\]{}:"\\^~])*)
''', re.VERBOSE)

_KEYWORDS = {"AND": "and", "OR": "or", "NOT": "not", "TO": "to"}


@dataclass
class _Token:
    kind: str
    text: str
    pos: int


def _tokenize(query: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(query):
        match = _TOKEN_RE.match(query, pos)
        if not match:
            raise SearchQueryError(
                "Could not parse query at position %d: %r" % (pos, query))
        kind = match.lastgroup
        text = match.group()
        pos = match.end()
        assert kind
        if kind == "ws" or kind == "boost" or kind == "fuzzy":
            continue
        if kind == "op":
            kind = {"&&": "and", "||": "or", "!": "not"}[text]
        elif kind == "word" and text in _KEYWORDS:
            kind = _KEYWORDS[text]
        tokens.append(_Token(kind, text, match.start()))
    return tokens


def unescape(value: str) -> str:
    """Remove Lucene backslash escapes."""
    return re.sub(r"\\(.)", r"\1", value)


class _Parser:
    def __init__(self, query: str):
        self.query = query
        self.tokens = _tokenize(query)
        self.index = 0

    # -- helpers -----------------------------------------------------------

    def peek(self, offset: int = 0) -> Optional[_Token]:
        index = self.index + offset
        if index < len(self.tokens):
            return self.tokens[index]
        return None

    def take(self, kind: Optional[str] = None) -> _Token:
        token = self.peek()
        if token is None or (kind and token.kind != kind):
            expected = kind or "more input"
            found = token.text if token else "end of query"
            raise SearchQueryError(
                "Could not parse query %r: expected %s, found %s" % (
                    self.query, expected, found))
        self.index += 1
        return token

    def at(self, *kinds: str) -> bool:
        token = self.peek()
        return token is not None and token.kind in kinds

    # -- grammar -----------------------------------------------------------

    def parse(self) -> Node:
        if not self.tokens:
            return MatchAll()
        node = self.expression(None)
        if self.peek() is not None:
            raise SearchQueryError(
                "Could not parse query %r: unexpected %r" % (
                    self.query, self.peek().text))  # type: ignore
        return node

    def expression(self, field: Optional[str]) -> Node:
        left = self.conjunction(field)
        while self.at("or"):
            self.take()
            right = self.conjunction(field)
            left = Or(left, right)
        return left

    def conjunction(self, field: Optional[str]) -> Node:
        left = self.clause(field)
        while True:
            if self.at("and"):
                self.take()
            elif not self.at("or", "rparen") and self.peek() is not None:
                pass    # implicit operator, q.op=AND
            else:
                break
            right = self.clause(field)
            left = And(left, right)
        return left

    def clause(self, field: Optional[str]) -> Node:
        if self.at("not"):
            self.take()
            return Not(self.clause(field))
        if self.at("minus", "plus"):
            modifier = self.take()
            if self.at("minus", "plus"):
                raise SearchQueryError(
                    "Could not parse query %r: unexpected %r after %r" % (
                        self.query, self.peek().text,  # type: ignore
                        modifier.text))
            if modifier.kind == "minus":
                return Not(self.primary(field))
        return self.primary(field)

    def primary(self, field: Optional[str]) -> Node:
        if self.at("lparen"):
            self.take()
            node = self.expression(field)
            self.take("rparen")
            return node

        if self.at("word") and self.peek(1) is not None \
                and self.peek(1).kind == "colon":  # type: ignore
            if field is not None:
                raise SearchQueryError(
                    "Could not parse query %r: nested field %r inside %r"
                    % (self.query, self.peek().text, field))  # type: ignore
            name = unescape(self.take("word").text)
            self.take("colon")
            if name == "*" and self.at("word") and \
                    self.peek().text == "*":  # type: ignore
                self.take()
                return MatchAll()
            return self.value(name)

        return self.value(field)

    def value(self, field: Optional[str]) -> Node:
        if self.at("lparen"):
            self.take()
            node = self.expression(field)
            self.take("rparen")
            return node
        if self.at("lrange"):
            return self.range(field)
        if self.at("quoted"):
            text = self.take().text[1:-1]
            return Term(field, unescape(text), phrase=True)
        if self.at("minus") and self.peek(1) is not None \
                and self.peek(1).kind == "word":  # type: ignore
            # negative number as a field value, e.g. f:-1
            self.take()
            return Term(field, "-" + _unescape_keep_wildcards(
                self.take().text))
        if self.at("word"):
            text = self.take().text
            if field is None and text == "*":
                return MatchAll()
            return Term(field, _unescape_keep_wildcards(text))
        found = self.peek()
        raise SearchQueryError(
            "Could not parse query %r: expected a value, found %s" % (
                self.query, found.text if found else "end of query"))

    def range(self, field: Optional[str]) -> Node:
        include_low = self.take("lrange").text == "["
        low = self.bound()
        self.take("to")
        high = self.bound()
        include_high = self.take("rrange").text == "]"
        return Range(field, low, high, include_low, include_high)

    def bound(self) -> Optional[str]:
        if self.at("quoted"):
            return unescape(self.take().text[1:-1])
        # an unquoted bound runs until TO or the closing bracket; colons
        # and signs are part of it (dates, date math, negative numbers)
        parts: list[str] = []
        while self.at("word", "colon", "minus", "plus"):
            parts.append(self.take().text)
        if not parts:
            self.take("word")   # raises the standard error
        text = "".join(parts)
        if text == "*":
            return None
        return unescape(text)


def _unescape_keep_wildcards(text: str) -> str:
    # escaped wildcards become literal characters, unescaped ones are kept
    # as wildcards; the compiler tells them apart by the escape
    return re.sub(r"\\([^*?])", r"\1", text)


def parse(query: str) -> Node:
    """Parse ``query`` and return the AST. Raises SearchQueryError."""
    return _Parser(query).parse()
