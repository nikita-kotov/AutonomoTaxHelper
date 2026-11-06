"""Modelo 115: the IRPF the user withheld from landlords, per quarter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .calc import Entry, quarter_of

RENT_CATEGORY_CODES = ("rent", "office_rent")  # expenses whose retention is declared in modelo 115


@dataclass
class Rent115:
    """One quarter of modelo 115: what the user withheld from landlords (not cumulative)."""
    year: int
    quarter: int
    payees: int = 0  # casilla 01, distinct recipients
    base: int = 0  # casilla 02, payments subject to retention
    withheld: int = 0  # casilla 03, retentions
    lines: List[Entry] = field(default_factory=list)
    other_withheld: int = 0  # entries with a retention that are not rent (modelo 111 territory), not included

    @property
    def result(self) -> int:
        """Casilla 05, the amount to pay."""
        return self.withheld


def build_115_year(entries: List[Entry], year: int) -> List[Rent115]:
    """Modelo 115 for the four quarters of a year.

    An expense counts when it carries a retention and its category is a rent category; the entry date is taken
    as the payment date. A recipient is identified by NIF, or by name when there is no NIF.
    """
    quarters = [Rent115(year, q) for q in (1, 2, 3, 4)]
    recipients: List[set] = [set() for _ in quarters]
    for e in entries:
        if e.date.year != year or e.kind != "expense" or e.withholding_cents <= 0:
            continue
        r = quarters[quarter_of(e.date) - 1]
        if e.category_code not in RENT_CATEGORY_CODES:
            r.other_withheld += 1
            continue
        r.base += e.base_cents
        r.withheld += e.withholding_cents
        r.lines.append(e)
        recipients[r.quarter - 1].add((e.vat_id or e.counterparty).strip().upper())
    for r, who in zip(quarters, recipients):
        r.payees = len(who)
    return quarters
