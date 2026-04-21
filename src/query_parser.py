from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any


MINUTE_PATTERN = re.compile(r"(?i)\bm(\d{1,3}|500)\.")


@dataclass(frozen=True)
class QueryMinuteInfo:
    minute_refs: tuple[int, ...]
    entry_minute: int


@dataclass(frozen=True)
class _QueryToken:
    type: str
    value: Any


def extract_minute_refs(query: str) -> list[int]:
    refs: list[int] = []
    for match in MINUTE_PATTERN.finditer(query or ""):
        refs.append(int(match.group(1)))
    return sorted(set(refs))


def infer_entry_minute(query: str) -> int:
    refs = extract_minute_refs(query)
    if not refs:
        return 1

    non_final = [minute for minute in refs if minute != 500]
    if non_final:
        return max(non_final)
    return 500


def infer_final_minute(query: str) -> int:
    refs = extract_minute_refs(query)
    if not refs:
        return 500

    non_final = [minute for minute in refs if minute != 500]
    if non_final:
        return max(non_final)
    return 500


def rewrite_query_minute_refs(query: str, source_minute: int | None, target_minute: int) -> str:
    if not query:
        return ""
    if source_minute is None:
        return query.strip()

    source_text = int(source_minute)
    target_text = int(target_minute)
    if source_text == target_text:
        return query.strip()

    pattern = re.compile(rf"(?i)\bm{source_text}\.")
    return pattern.sub(f"m{target_text}.", query.strip())


def strip_minute_prefixes(query: str) -> str:
    return MINUTE_PATTERN.sub("", query or "")


def absolute_to_period_minute(minute: int) -> tuple[int, int]:
    if minute <= 0:
        return 1, 1
    if minute == 500:
        return 2, 90
    if minute <= 45:
        return 1, minute
    return 2, minute


def period_minute_to_absolute(period: int, minute: int) -> int:
    return minute


class _QueryExpressionParser:
    def __init__(self, query: str) -> None:
        self.tokens = self._tokenize(query)
        self.position = 0

    @staticmethod
    def _tokenize(query: str) -> list[_QueryToken]:
        tokens: list[_QueryToken] = []
        text = query or ""
        length = len(text)
        index = 0
        while index < length:
            char = text[index]
            if char.isspace():
                index += 1
                continue
            if text.startswith("<=", index) or text.startswith(">=", index) or text.startswith("!=", index) or text.startswith("<>", index):
                tokens.append(_QueryToken("OP", text[index : index + 2]))
                index += 2
                continue
            if char in "()+-*/<>=,":
                token_type = {
                    "(": "LPAREN",
                    ")": "RPAREN",
                    "+": "PLUS",
                    "-": "MINUS",
                    "*": "STAR",
                    "/": "SLASH",
                    "<": "OP",
                    ">": "OP",
                    "=": "OP",
                    ",": "COMMA",
                }[char]
                tokens.append(_QueryToken(token_type, char))
                index += 1
                continue
            if char == '"':
                start = index
                index += 1
                escaped = False
                while index < length:
                    current = text[index]
                    if current == '"' and not escaped:
                        index += 1
                        break
                    if current == "\\" and not escaped:
                        escaped = True
                    else:
                        escaped = False
                    index += 1
                raw_value = text[start:index]
                try:
                    value = ast.literal_eval(raw_value)
                except Exception:
                    value = raw_value.strip('"')
                tokens.append(_QueryToken("STRING", value))
                continue
            if char.isdigit() or (char == "." and index + 1 < length and text[index + 1].isdigit()):
                start = index
                index += 1
                while index < length and (text[index].isdigit() or text[index] == "."):
                    index += 1
                raw = text[start:index]
                tokens.append(_QueryToken("NUMBER", float(raw) if "." in raw else int(raw)))
                continue
            if char.isalpha() or char == "_":
                start = index
                index += 1
                while index < length and (text[index].isalnum() or text[index] == "_"):
                    index += 1
                word = text[start:index]
                lowered = word.lower()
                token_type = {
                    "and": "AND",
                    "or": "OR",
                    "not": "NOT",
                    "between": "BETWEEN",
                }.get(lowered, "IDENT")
                tokens.append(_QueryToken(token_type, word))
                continue
            raise ValueError(f"Caractere inesperado na consulta: {char!r}")
        tokens.append(_QueryToken("EOF", None))
        return tokens

    def _peek(self) -> _QueryToken:
        return self.tokens[self.position]

    def _advance(self) -> _QueryToken:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def _match(self, *token_types: str) -> _QueryToken | None:
        if self._peek().type in token_types:
            return self._advance()
        return None

    def _expect(self, token_type: str) -> _QueryToken:
        token = self._match(token_type)
        if token is None:
            raise ValueError(f"Esperado {token_type} na consulta.")
        return token

    def parse(self) -> Any:
        expression = self._parse_or()
        if self._peek().type != "EOF":
            raise ValueError("Consulta com tokens excedentes.")
        return expression

    def _parse_or(self) -> Any:
        node = self._parse_and()
        while self._match("OR"):
            node = ("or", node, self._parse_and())
        return node

    def _parse_and(self) -> Any:
        node = self._parse_comparison()
        while self._match("AND"):
            node = ("and", node, self._parse_comparison())
        return node

    def _parse_comparison(self) -> Any:
        node = self._parse_arithmetic()
        if self._match("BETWEEN"):
            lower = self._parse_arithmetic()
            self._expect("AND")
            upper = self._parse_arithmetic()
            return ("between", node, lower, upper)
        operator = self._match("OP")
        if operator is not None:
            right = self._parse_arithmetic()
            return ("compare", operator.value, node, right)
        return node

    def _parse_arithmetic(self) -> Any:
        node = self._parse_term()
        while True:
            if self._match("PLUS"):
                node = ("add", node, self._parse_term())
                continue
            if self._match("MINUS"):
                node = ("sub", node, self._parse_term())
                continue
            return node

    def _parse_term(self) -> Any:
        node = self._parse_factor()
        while True:
            if self._match("STAR"):
                node = ("mul", node, self._parse_factor())
                continue
            if self._match("SLASH"):
                node = ("div", node, self._parse_factor())
                continue
            return node

    def _parse_factor(self) -> Any:
        if self._match("NOT"):
            return ("not", self._parse_factor())
        if self._match("PLUS"):
            return self._parse_factor()
        if self._match("MINUS"):
            return ("neg", self._parse_factor())
        token = self._peek()
        if token.type == "LPAREN":
            self._advance()
            node = self._parse_or()
            self._expect("RPAREN")
            return node
        if token.type == "NUMBER":
            self._advance()
            return ("literal", token.value)
        if token.type == "STRING":
            self._advance()
            return ("literal", token.value)
        if token.type == "IDENT":
            self._advance()
            return ("name", token.value)
        raise ValueError(f"Token inesperado na consulta: {token.type}")


def _lookup_snapshot_value(snapshot: dict[str, Any], name: str) -> Any:
    if name in snapshot:
        value = snapshot.get(name)
        return 0 if value is None else value
    lowered = name.lower()
    for key, value in snapshot.items():
        if str(key).lower() == lowered:
            return 0 if value is None else value
    return 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_compare(left: Any, right: Any, operator: str) -> bool:
    if operator == "=":
        return left == right
    if operator in {"!=", "<>"}:
        return left != right
    if _is_number(left) and _is_number(right):
        left_value = float(left)
        right_value = float(right)
    elif isinstance(left, str) and isinstance(right, str):
        left_value = left
        right_value = right
    else:
        return False
    if operator == "<":
        return left_value < right_value
    if operator == "<=":
        return left_value <= right_value
    if operator == ">":
        return left_value > right_value
    if operator == ">=":
        return left_value >= right_value
    raise ValueError(f"Operador desconhecido: {operator}")


def _evaluate_query_node(node: Any, snapshot: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "literal":
        return node[1]
    if kind == "name":
        return _lookup_snapshot_value(snapshot, str(node[1]))
    if kind == "neg":
        return -float(_evaluate_query_node(node[1], snapshot))
    if kind == "not":
        return not bool(_evaluate_query_node(node[1], snapshot))
    if kind == "add":
        return _evaluate_query_node(node[1], snapshot) + _evaluate_query_node(node[2], snapshot)
    if kind == "sub":
        return _evaluate_query_node(node[1], snapshot) - _evaluate_query_node(node[2], snapshot)
    if kind == "mul":
        return _evaluate_query_node(node[1], snapshot) * _evaluate_query_node(node[2], snapshot)
    if kind == "div":
        return _evaluate_query_node(node[1], snapshot) / _evaluate_query_node(node[2], snapshot)
    if kind == "and":
        return bool(_evaluate_query_node(node[1], snapshot)) and bool(_evaluate_query_node(node[2], snapshot))
    if kind == "or":
        return bool(_evaluate_query_node(node[1], snapshot)) or bool(_evaluate_query_node(node[2], snapshot))
    if kind == "between":
        value = _evaluate_query_node(node[1], snapshot)
        lower = _evaluate_query_node(node[2], snapshot)
        upper = _evaluate_query_node(node[3], snapshot)
        return _safe_compare(lower, value, "<=") and _safe_compare(value, upper, "<=")
    if kind == "compare":
        operator = node[1]
        left = _evaluate_query_node(node[2], snapshot)
        right = _evaluate_query_node(node[3], snapshot)
        return _safe_compare(left, right, operator)
    raise ValueError(f"Nó de consulta desconhecido: {kind}")


def evaluate_snapshot_query(query: str, snapshot: dict[str, Any]) -> bool:
    cleaned_query = strip_minute_prefixes(query or "").strip()
    if not cleaned_query:
        return True
    parser = _QueryExpressionParser(cleaned_query)
    tree = parser.parse()
    return bool(_evaluate_query_node(tree, snapshot))
