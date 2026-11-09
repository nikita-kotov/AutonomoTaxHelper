"""Closed sets of codes that the database and the forms keep as text.

They are `StrEnum`s, so a member equals its stored text (`Regime.DOMESTIC == "domestic"`), prints as it and binds to
SQL like it; saved data and templates need no change. Converting stored text with `Regime(text)` raises ValueError for
an unknown code, which is how damaged or foreign data is caught when it is loaded.
"""
from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    """Whether an entry (or a contact) is income or expense."""
    INCOME = "income"
    EXPENSE = "expense"


class Regime(StrEnum):
    """The IVA treatment of an entry. The first four apply to income, the rest to expenses (`domestic` to both)."""
    DOMESTIC = "domestic"
    EXEMPT = "exempt"
    EU_B2B = "eu_b2b"
    NON_EU = "non_eu"
    EU_REVERSE = "eu_reverse"
    NON_EU_REVERSE = "non_eu_reverse"
    NO_VAT = "no_vat"


class DeductionMode(StrEnum):
    """How much of an expense category counts for the IRPF or for the IVA."""
    FULL = "full"
    HOME_SUPPLY = "home_supply"  # electricity, water, internet: the home-office share
    HOME_PROPERTY = "home_property"  # rent, IBI: the home-office share
    NONE = "none"


class HomeRule(StrEnum):
    """The IRPF rule for home costs: the affected percentage, or the affected percentage times the supplies factor."""
    SHARE = "share"
    SHARE_FACTOR = "share_factor"


class VatHomeMode(StrEnum):
    """The IVA rule for home supplies: like `HomeRule`, or no deduction at all."""
    SHARE = "share"
    SHARE_FACTOR = "share_factor"
    NONE = "none"


class IrpfMethod(StrEnum):
    """The estimación directa variant."""
    SIMPLIFICADA = "simplificada"
    NORMAL = "normal"


class PaymentMethod(StrEnum):
    """How the amount of a declaration is paid."""
    NRC = "nrc"
    DOMICILIACION = "domiciliacion"


class VatNegative(StrEnum):
    """What a negative IVA result of the fourth quarter asks for."""
    COMPENSATE = "compensate"
    REFUND = "refund"


class FxMode(StrEnum):
    """Where exchange rates for foreign-currency entries come from."""
    AUTO = "auto"
    MANUAL = "manual"


class FilingStatus(StrEnum):
    """The stored status of a tracked filing."""
    PENDING = "pending"
    FILED = "filed"
    NOT_REQUIRED = "not_required"
