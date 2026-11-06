"""Builds the modelo 303 flat file (layout "Ejercicio 2026 y siguientes", DR303e26v101).

Covers an autonomo in regimen general / territorio comun, quarterly periods.
Pages emitted: DP30300 (header), DP30301 (liquidacion), DP30303 (informacion
adicional y resultado) and DP303DID (bank account, only for domiciliacion/devolucion).
Pages for regimen simplificado (30302), exoneration from the 390 (30304) and
prorrata (30305) are outside this builder's scope.
"""
from __future__ import annotations

from ..calc import Vat303
from ..i18n import UserError
from .fixed import Record, Taxpayer, an, blanks, const, file_footer, file_header, num, pct, signed

PAGE1_LENGTH = 1581
PAGE3_LENGTH = 1017
DID_LENGTH = 823


def declaration_type(rep: Vat303, iban: str = "", refund_iban: str = "") -> str:
    """Tipo: I ingreso, U domiciliacion, N cero, C compensar, D devolucion (resultado negativo).

    `iban` is the account of a direct debit. `refund_iban` is given when the taxpayer asks for the refund of a
    negative result: it is honoured in the 4th quarter only, the other quarters can only compensate.
    """
    if rep.c71 > 0:
        return "U" if iban.strip() else "I"
    if rep.c71 == 0:
        return "N"
    return "D" if refund_iban.strip() and rep.quarter == 4 else "C"


def _period(rep: Vat303) -> str:
    return f"{rep.quarter}T"


def _flags(rep: Vat303) -> str:
    """Identification flags, positions 109-130, for territorio comun + regimen general."""
    last = rep.quarter == 4
    f = []
    f.append("2")                      # 109 tributacion exclusivamente foral
    f.append("2")                      # 110 registro devolucion mensual
    f.append("3")                      # 111 solo regimen general
    f.append("2")                      # 112 autoliquidacion conjunta
    f.append("2")                      # 113 criterio de caja
    f.append("2")                      # 114 destinatario criterio de caja
    f.append("2")                      # 115 opcion prorrata especial (Nota 6: "2" = NO / blank)
    f.append("2")                      # 116 revocacion prorrata especial (Nota 6)
    f.append("2")                      # 117 concurso de acreedores
    f.append(blanks(8))                # 118-125 fecha del auto de concurso
    f.append(" ")                      # 126 tipo de autoliquidacion concursal
    f.append("2")                      # 127 SII voluntario
    f.append("2" if last else "0")     # 128 exonerado del modelo 390 (Nota 4)
    f.append("0")                      # 129 volumen anual de operaciones (Nota 3)
    f.append("0")                      # 130 gasolinas (Nota 8, periodos trimestrales)
    return "".join(f)


def page1(rep: Vat303, tp: Taxpayer, tipo: str) -> str:
    """DP30301: identification and regimen general liquidation (1581 chars)."""
    r = Record(PAGE1_LENGTH, "DP30301")
    r.add("<T303").add("01000").add(">")
    r.add(" ")                                       # complementary-page flag
    r.add(tipo)
    r.add(an(tp.nif, 9)).add(an(tp.full_name, 80))
    r.add(f"{rep.year:04d}").add(_period(rep))
    r.add(_flags(rep))

    def triple(base: int, rate_x100: int, quota: int) -> None:
        r.add(num(base)).add(pct(rate_x100)).add(num(quota))

    # IVA devengado, regimen general. Order follows the layout.
    triple(0, 0, 0)                                  # [150][151][152]
    triple(0, 0, 0)                                  # [165][166][167]
    triple(*(rep.by_rate[4][0], 400, rep.by_rate[4][1]))     # [01][02][03] 4%
    triple(0, 0, 0)                                  # [153][154][155]
    triple(*(rep.by_rate[10][0], 1000, rep.by_rate[10][1]))  # [04][05][06] 10%
    triple_21 = (rep.by_rate[21][0], 2100, rep.by_rate[21][1])
    triple(*triple_21)                               # [07][08][09] 21%
    r.add(num(rep.c10)).add(num(rep.c11))            # [10][11] adquisiciones intracomunitarias
    r.add(num(rep.c12)).add(num(rep.c13))            # [12][13] otras ISP
    r.add(signed(rep.c14)).add(signed(rep.c15))      # [14][15] modificacion bases y cuotas
    # Recargo de equivalencia (not used): constants for the rate columns
    r.add(num(0)).add(pct(175)).add(num(0))          # [156][157][158]
    r.add(num(0)).add(pct(50)).add(num(0))           # [168][169][170]
    r.add(num(0)).add(pct(0)).add(num(0))            # [16][17][18]
    r.add(num(0)).add(pct(140)).add(num(0))          # [19][20][21]
    r.add(num(0)).add(pct(520)).add(num(0))          # [22][23][24]
    r.add(signed(0)).add(signed(0))                  # [25][26]
    r.add(signed(rep.c27))                           # [27] total cuota devengada
    # IVA deducible
    r.add(num(rep.c28)).add(num(rep.c29))            # [28][29] interiores corrientes
    r.add(num(0)).add(num(0))                        # [30][31] interiores bienes de inversion
    r.add(num(0)).add(num(0))                        # [32][33] importaciones corrientes
    r.add(num(0)).add(num(0))                        # [34][35] importaciones inversion
    r.add(num(rep.c36)).add(num(rep.c37))            # [36][37] intracomunitarias corrientes
    r.add(num(0)).add(num(0))                        # [38][39] intracomunitarias inversion
    r.add(signed(rep.c40)).add(signed(rep.c41))      # [40][41] rectificacion de deducciones
    r.add(signed(0))                                 # [42] compensaciones agricultura
    r.add(signed(0))                                 # [43] regularizacion inversiones
    r.add(signed(0))                                 # [44] prorrata definitiva
    r.add(signed(rep.c45))                           # [45] total a deducir
    r.add(signed(rep.c46))                           # [46] resultado regimen general
    r.add(blanks(521)).add(blanks(13))
    r.add(const("</T30301000>", 12))
    return r.text()


def page3(rep: Vat303) -> str:
    """DP30303: informacion adicional and resultado (1017 chars)."""
    r = Record(PAGE3_LENGTH, "DP30303")
    r.add("<T303").add("03000").add(">")
    r.add(signed(rep.info_eu_services_base))         # [59] entregas intracomunitarias de bienes y servicios
    r.add(signed(0))                                 # [60] exportaciones
    r.add(signed(rep.info_non_eu_base))              # [120] no sujetas por reglas de localizacion
    r.add(signed(0))                                 # [122] sujetas con ISP
    r.add(signed(0)).add(signed(0))                  # [123][124] ventanilla unica
    r.add(signed(0)).add(signed(0))                  # [62][63] criterio de caja (devengo general)
    r.add(signed(0)).add(signed(0))                  # [74][75] criterio de caja adquisiciones
    r.add(signed(0))                                 # [76] regularizacion art. 80.cinco.5a
    r.add(signed(rep.c64))                           # [64] suma de resultados
    r.add("10000")                                   # [65] 100,00 % Administracion del Estado
    r.add(signed(rep.c66))                           # [66]
    r.add(num(0))                                    # [77] IVA importacion aduana
    r.add(num(rep.carry_in))                         # [110] compensaciones pendientes anteriores
    r.add(num(rep.c78))                              # [78] aplicadas en este periodo
    r.add(num(rep.carry_in - rep.c78))               # [87] pendientes para periodos posteriores
    r.add(signed(0))                                 # [68] regularizacion anual
    r.add(signed(0))                                 # [108] otros ajustes
    r.add(signed(rep.c69))                           # [69] resultado de la autoliquidacion
    r.add(num(0))                                    # [70] ingresos anteriores
    r.add(num(0))                                    # [109] devoluciones acordadas
    r.add(num(0))                                    # [112] pago a cuenta gasolinas
    r.add(signed(rep.c71))                           # [71] resultado
    r.add(" ").add(" ").add(blanks(13)).add(" ")     # sin actividad, rectificativa, justificante, baja domiciliacion
    r.add(num(0))                                    # [111] importe de la rectificacion
    r.add(" ").add(" ")                              # motivos de rectificacion
    r.add(blanks(546))
    r.add(const("</T30303000>", 12))
    return r.text()


def page_did(iban: str = "", sepa: str = "0") -> str:
    """DP303DID: bank account page (823 chars). Empty (marca "0") unless domiciliacion."""
    r = Record(DID_LENGTH, "DP303DID")
    r.add("<T303").add("DID00").add(">")
    r.add(blanks(11))                                # SWIFT-BIC
    r.add(an(iban.replace(" ", ""), 34))
    r.add(blanks(70)).add(blanks(35)).add(blanks(30)).add(blanks(2))
    r.add(sepa)                                      # marca SEPA: 0 vacia, 1 cuenta Espana
    r.add(blanks(617))
    r.add(const("</T303DID00>", 12))
    return r.text()


def build(rep: Vat303, tp: Taxpayer, tipo: str = None, iban: str = "",
          dev_nif: str = "", dev_version: str = "") -> str:
    """Full file content for the declaration (no line breaks)."""
    tp.validate()
    tipo = tipo or declaration_type(rep, iban)
    if tipo not in ("I", "U", "N", "C", "D", "G", "V", "X"):
        raise ValueError(f"unsupported tipo de declaracion {tipo!r}")
    if tipo in ("U", "D") and not iban.strip():
        raise UserError("err.iban_required")
    parts = [file_header("303", rep.year, _period(rep), dev_nif or tp.nif, dev_version),
             page1(rep, tp, tipo), page3(rep),
             page_did(iban if tipo in ("U", "D") else "", sepa="1" if tipo in ("U", "D") else "0")]
    parts.append(file_footer("303", rep.year, _period(rep)))
    return "".join(parts)
