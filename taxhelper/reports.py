"""One entry point for the tax reports: it joins the user's settings, the year's entries and the
IVA balance carried in, so callers never repeat that plumbing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from . import calc, rent115, renta
from .repositories import EntryRepository
from .settings import SettingsRepository


@dataclass(frozen=True)
class QuarterFigures:
    """What one quarter of the year came to: each amount is that quarter's own, not accumulated."""
    quarter: int
    income: int  # invoiced without IVA
    expenses: int  # the part of the expenses that is deductible for IRPF
    irpf: int  # payment of modelo 130 for the quarter (negative: credited later)
    vat: int  # result of modelo 303 for the quarter (negative: to compensate)


class TaxReports:
    def __init__(self, settings: SettingsRepository, entries: EntryRepository):
        self.settings = settings
        self.entries = entries

    @property
    def tax_settings(self) -> calc.TaxSettings:
        return self.settings.load().tax()

    def year_entries(self, year: int) -> List[calc.Entry]:
        return self.entries.year_entries(year)

    def vat_year(self, year: int, upto: int = 4) -> List[calc.Vat303]:
        """Modelo 303 for quarters 1..upto, negative balances carried forward."""
        return calc.build_303_year(self.year_entries(year), self.tax_settings, year, upto,
                                   self.settings.carry_in(year))

    def irpf_year(self, year: int, upto: int = 4) -> List[calc.Irpf130]:
        """Modelo 130 for quarters 1..upto (each one cumulative)."""
        return calc.build_130_year(self.year_entries(year), self.tax_settings, year, upto)

    def vat_detail(self, year: int, quarter: int) -> List[dict]:
        """The quarter's entries that touch the 303, each with the IVA deductible from it."""
        tax = self.tax_settings
        return [{"e": e, "vat_ded": calc.vat_deductible(e, tax)} for e in self.year_entries(year)
                if calc.quarter_of(e.date) == quarter and (e.vat_cents or e.regime != "domestic")]

    def irpf_by_category(self, year: int, quarter: int) -> Dict[str, int]:
        """Deductible IRPF expense per category, accumulated from 1 January to the end of the quarter."""
        entries = [e for e in self.year_entries(year) if e.date <= calc.quarter_end(year, quarter)]
        by_category: Dict[str, int] = {}
        for e, amount in zip(entries, calc.irpf_deductions(entries, self.tax_settings)):
            if e.kind == "expense":
                by_category[e.category] = by_category.get(e.category, 0) + amount
        return by_category

    def overview(self, year: int) -> List[QuarterFigures]:
        """The four quarters side by side: income, deductible expenses and the taxes of each quarter."""
        entries = self.year_entries(year)
        deductions = calc.irpf_deductions(entries, self.tax_settings)
        irpf, vat = self.irpf_year(year), self.vat_year(year)
        rows = []
        for q in (1, 2, 3, 4):
            in_quarter = [(e, d) for e, d in zip(entries, deductions) if calc.quarter_of(e.date) == q]
            rows.append(QuarterFigures(
                q, sum(e.base_cents for e, _ in in_quarter if e.kind == "income"),
                sum(d for e, d in in_quarter if e.kind == "expense"), irpf[q - 1].c19, vat[q - 1].c71))
        return rows

    def renta_estimate(self, year: int, paid_130: Dict[int, int], upto: int) -> renta.RentaEstimate:
        """The estimated Renta of the year from the figures so far.

        `paid_130` holds the result of the quarters already filed (quarter -> cents); the other quarters up to
        `upto` count with the amount the app calculates for them.
        """
        settings = self.settings.load()
        annual = self.annual(year)
        advances = sum(paid_130.get(r.quarter, r.c19) for r in self.irpf_year(year, upto))
        return renta.estimate_renta(
            annual.net_yield_reduced, settings.renta_other_income_cents, settings.renta_personal_minimum_cents,
            annual.withholdings, advances, settings.regional_scale)

    def retentions_115(self, year: int) -> List[rent115.Rent115]:
        """Modelo 115 (retentions on rent) for the four quarters of the year; each quarter stands alone."""
        return rent115.build_115_year(self.year_entries(year), year)

    def annual(self, year: int) -> calc.Annual:
        return calc.build_annual(self.year_entries(year), self.tax_settings, year, self.settings.carry_in(year))
