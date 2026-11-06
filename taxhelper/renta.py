"""A rough estimate of the annual income tax (Renta, modelo 100) from the year's figures so far.

The estimate applies the general scale (state part plus the regional part) to the net yield of the activity and
other income, takes off the tax on the personal minimum, and subtracts what was already paid: the withholdings
and the quarterly payments of modelo 130. It is a forecast for the dashboard: no other deductions are included.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from .calc import round_cents

# A scale is a list of brackets (lower limit in cents, rate in percent): each rate applies to the part of the base
# from its limit up to the next limit. The first limit is 0.
Scale = Tuple[Tuple[int, Decimal], ...]

# State part of the general scale (art. 63 LIRPF); the same since 2021.
STATE_SCALE: Scale = (
    (0, Decimal("9.5")), (1_245_000, Decimal("12")), (2_020_000, Decimal("15")),
    (3_520_000, Decimal("18.5")), (6_000_000, Decimal("22.5")), (30_000_000, Decimal("24.5")))

DEFAULT_PERSONAL_MINIMUM_CENTS = 555_000  # art. 57 LIRPF


def scale_tax(base_cents: int, scale: Scale) -> int:
    """The tax in cents that a scale charges on a base (zero for a base that is not positive)."""
    if base_cents <= 0:
        return 0
    total = Decimal(0)
    for i, (start, rate) in enumerate(scale):
        end = scale[i + 1][0] if i + 1 < len(scale) else None
        if base_cents <= start:
            break
        part = (min(base_cents, end) if end is not None else base_cents) - start
        total += Decimal(part) * rate / 100
    return round_cents(total)


def parse_scale(text: str) -> Optional[Scale]:
    """A scale from text with one bracket per line: the amount in euros where it starts, then the rate in percent
    (`12500 12.5`). Empty text gives None, meaning the default scale. Raises ValueError for anything unreadable:
    the first bracket must start at 0 and the limits must increase."""
    brackets = []
    for line in text.replace(";", "\n").splitlines():
        parts = line.replace(",", ".").split()
        if not parts:
            continue
        if len(parts) != 2:
            raise ValueError(line)
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", parts[0]):
            raise ValueError(line)  # 12,500 or 12.500: a thousands separator, ambiguous with a decimal point
        try:
            start, rate = Decimal(parts[0]), Decimal(parts[1])
        except InvalidOperation:
            raise ValueError(line) from None
        if start < 0 or not (0 <= rate <= 100):
            raise ValueError(line)
        brackets.append((int(round_cents(start * 100)), rate))
    if not brackets:
        return None
    if brackets[0][0] != 0 or any(b[0] >= n[0] for b, n in zip(brackets, brackets[1:])):
        raise ValueError(text)
    return tuple(brackets)


@dataclass(frozen=True)
class RentaEstimate:
    net_yield: int  # of the activity, after the start-of-activity reduction if it applies
    other_income: int
    base: int  # what the scale is applied to
    personal_minimum: int
    tax: int  # the scale on the base minus the scale on the personal minimum, never below zero
    withholdings: int
    advances: int  # quarterly payments of modelo 130
    result: int  # positive: still to pay when the return is filed; negative: a refund


def estimate_renta(net_yield: int, other_income: int, personal_minimum: int, withholdings: int, advances: int,
                   regional_scale: Optional[Scale] = None) -> RentaEstimate:
    """The estimate for the year; `regional_scale` None means the default regional scale, equal to the state one."""
    regional = regional_scale or STATE_SCALE
    base = net_yield + other_income
    tax = max(0, scale_tax(base, STATE_SCALE) + scale_tax(base, regional)
              - scale_tax(personal_minimum, STATE_SCALE) - scale_tax(personal_minimum, regional))
    return RentaEstimate(net_yield, other_income, base, personal_minimum, tax, withholdings, advances,
                         tax - withholdings - advances)
