# Task: Arithmetic Expression Evaluator

## Goal

Implement a function

```python
def evaluate(expression: str) -> float:
    ...
```

in `candidate.py` that evaluates a basic arithmetic expression given as a
string and returns the numeric result as a `float`.

## Supported syntax

- Non-negative integer and decimal literals, e.g. `3`, `42`, `3.5`.
- Binary operators: `+`, `-`, `*`, `/`.
- Unary `+` and unary `-`, e.g. `-5`, `-(2 + 3)`.
- Parentheses `(` `)` for grouping, arbitrarily nested.
- Arbitrary whitespace between tokens, which must be ignored.

## Operator precedence and associativity

- `*` and `/` bind more tightly than `+` and `-`.
- Operators of the **same** precedence level are evaluated **strictly
  left-to-right** (standard left-associativity). For example:
  - `10 - 2 - 3` must evaluate as `(10 - 2) - 3 = 5`, **not**
    `10 - (2 - 3) = 11`.
  - `100 / 10 / 2` must evaluate as `(100 / 10) / 2 = 5.0`, **not**
    `100 / (10 / 2) = 20.0`.
  - `10 - 2 + 3` must evaluate strictly left-to-right as `(10 - 2) + 3 = 11`.
- Parentheses always override default precedence/associativity.

## Division semantics

- `/` performs true (floating-point) division: `7 / 2 == 3.5`, and
  `8 / 2 == 4.0` (a `float`, not an `int`).
- Dividing by zero raises `ZeroDivisionError`.

## Error handling

`evaluate` raises `ValueError` for any malformed input, including but not
limited to:

- An empty string, or a string containing no tokens.
- Unknown characters (anything other than digits, `.`, `()+-*/`, and
  whitespace).
- Mismatched or unbalanced parentheses.
- Missing operands, e.g. a trailing operator (`"2 +"`).
- Leftover, unconsumed input after a complete expression has already been
  parsed, e.g. two numbers with no operator between them (`"2 3"`).

## Return type

`evaluate` always returns a `float`, even for expressions whose
mathematical result is a whole number.

## Out of scope

Exponentiation, modulo, variables, functions, and implicit multiplication
(e.g. `2(3)`) are not required and may raise `ValueError`.
