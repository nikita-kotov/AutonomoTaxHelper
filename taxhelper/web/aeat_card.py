"""Data for the «AEAT file and validation» card shown on the 303 and 130 pages."""
from __future__ import annotations

import json
from typing import Any, Dict

from ..aeat import service as aeat_service
from .infra import svc


def aeat_card(model: str, year: int, quarter: int) -> Dict[str, Any]:
    """What the card needs: the built file (or why it cannot be built) and the latest validation."""
    s = svc()
    card: Dict[str, Any] = {"model": model, "year": year, "q": quarter, "built": None, "error": None,
            "check": None, "errors": [], "warnings": [], "current": False}
    try:
        card["built"] = aeat_service.build_file(s.settings.load(), s.reports, model, year, quarter)
    except ValueError as exc:  # includes UserError (incomplete profile, amounts the layout cannot hold)
        card["error"] = str(exc)
        return card
    check = s.checks.latest(model, year, f"{quarter}T")
    if check:
        card.update(check=check, errors=json.loads(check["errors"]), warnings=json.loads(check["warnings"]),
                    current=check["file_sha256"] == card["built"].sha256)
    return card
