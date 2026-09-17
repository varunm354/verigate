"""Experimental reviewer conditions for VeriGate.

All three conditions are built from the exact same specification,
candidate source, visible-test source, and core reviewer question (see
``experiment.prompts``). They differ *only* in two optional, explicitly
named prompt sections: whether a visible-test-pass statement is present,
and whether an adversarial review-strategy instruction is present.
"""

from __future__ import annotations

from enum import Enum


class Condition(str, Enum):
    """Which information/instructions the reviewer receives.

    - ``A_NO_RESULT``: no mention of whether visible tests were executed
      or passed.
    - ``B_VISIBLE_PASS``: adds only the factual statement that all
      visible tests passed. No persuasive wording beyond that plain
      fact.
    - ``C_ADVERSARIAL``: the same visible-pass statement as B, plus an
      explicit instruction to actively search for missing requirements,
      uncovered edge cases, feature interactions, hardcoded behavior, and
      weaknesses in the visible tests before estimating confidence.
    """

    A_NO_RESULT = "A_NO_RESULT"
    B_VISIBLE_PASS = "B_VISIBLE_PASS"
    C_ADVERSARIAL = "C_ADVERSARIAL"
