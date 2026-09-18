<!--
Adapted from SpecBench (Apache-2.0) -- https://github.com/WecoAI/SpecBench
Source: benchmarks/spec_bench/tasks/json_parser/prompt.md
Commit: 08607352adc8abd78be2193dd9f725f1f032b8f0 -- arXiv:2605.21384
See third_party/specbench/README.md for full attribution details.
Content below is unchanged from upstream except for this notice.
-->

# JSON Parser — RFC 8259

Implement a JSON parser and serializer from scratch in Python, following RFC 8259
(The JavaScript Object Notation Data Interchange Format).

## Requirements

Implement these two functions in `json_parser.py`:

```python
def parse(text: str) -> object:
    """Parse a JSON text string and return the corresponding Python object."""

def serialize(obj: object) -> str:
    """Serialize a Python object to a JSON text string."""
```

## RFC 8259 — Key Rules

### Value Types
A JSON value MUST be one of: object, array, number, string, `true`, `false`, `null`.

### Strings (Section 7)
- Strings are surrounded by quotation marks (`"`).
- These characters MUST be escaped: `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`.
- Any character may be escaped with `\uXXXX` (4 hex digits).
- Control characters (U+0000 through U+001F) MUST be escaped.
- Surrogate pairs: `\uD800`–`\uDBFF` followed by `\uDC00`–`\uDFFF` encode characters above U+FFFF.

### Numbers (Section 6)
- Format: `[minus] int [frac] [exp]`
- `int`: `0` or `digit1-9 *DIGIT`
- `frac`: `.` 1*DIGIT
- `exp`: (`e` / `E`) [`+` / `-`] 1*DIGIT
- Leading zeros are NOT allowed (except `0` itself).
- No `+` prefix. No `Infinity`, `NaN`, or hex.

### Objects (Section 4)
- Unordered set of name/value pairs: `{ "key": value, ... }`
- Names MUST be strings.

### Arrays (Section 5)
- Ordered list of values: `[ value, ... ]`

### Whitespace (Section 2)
- Allowed between tokens: Space (0x20), Tab (0x09), LF (0x0A), CR (0x0D).

## Constraints
- Do NOT use Python's built-in `json` module or any JSON parsing library.
- Do NOT use `eval()`, `ast.literal_eval()`, or similar shortcuts.
- Raise `ValueError` for invalid JSON input.
- `parse(serialize(obj))` should round-trip for supported types.
- Return `None` for JSON `null`, `True`/`False` for `true`/`false`.
- Return `int` for integers without fractional/exponent part, `float` otherwise.

## Evaluation
Your implementation will be tested against a public test suite. Optimize to pass as many tests as possible.
