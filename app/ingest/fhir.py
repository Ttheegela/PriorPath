from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.models import Claim, LineItem


@dataclass(frozen=True)
class ParseError:
    path: str
    message: str


@dataclass
class ParseResult:
    claims: list[Claim] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


def pseudonym(patient_reference: str) -> str:
    return "P-" + hashlib.sha256(patient_reference.encode()).hexdigest()[:8]


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    if isinstance(exc, KeyError):
        return f"missing field {exc.args[0]!r}"
    return str(exc) or exc.__class__.__name__


def _charge(item: dict[str, Any]) -> Decimal:
    net = (item.get("net") or {}).get("value")
    if net is None:
        for adj in item.get("adjudication", []):
            codes = {c.get("code") for c in (adj.get("category") or {}).get("coding", [])}
            if "submitted" in codes:
                net = adj["amount"]["value"]
                break
    if net is None:
        raise ValueError("no charge: item.net and submitted adjudication missing")
    return Decimal(str(net))


def _units(item: dict[str, Any]) -> int:
    q = Decimal(str((item.get("quantity") or {}).get("value", 1)))
    if q != q.to_integral_value():
        raise ValueError(f"quantity must be a whole number, got {q}")
    return int(q)


def _parse_item(item: dict[str, Any], index: int, diag: dict[int, str], default_dos: str | None) -> LineItem:
    seq = int(item.get("sequence", index + 1))
    codings = item["productOrService"]["coding"]
    coding = next(
        (c for c in codings if any(s in str(c.get("system", "")).lower() for s in ("cpt", "hcpcs"))),
        codings[0],
    )
    dos_raw = item.get("servicedDate") or (item.get("servicedPeriod") or {}).get("start") or default_dos
    if not dos_raw:
        raise ValueError("no date of service")
    loc = item.get("locationCodeableConcept")
    return LineItem(
        id=f"L{seq}",
        code=coding["code"],
        modifiers=[m["coding"][0]["code"] for m in item.get("modifier", [])],
        units=_units(item),
        charge=_charge(item),
        date_of_service=date.fromisoformat(str(dos_raw)[:10]),
        place_of_service=loc["coding"][0]["code"] if loc else None,
        diagnosis_codes=[diag[s] for s in item.get("diagnosisSequence", []) if s in diag],
    )


def _parse_eob(eob: dict[str, Any], path: str, errors: list[ParseError]) -> Claim | None:
    eob_id = eob.get("id")
    if not eob_id:
        errors.append(ParseError(f"{path}.id", "missing id"))
        return None
    diag: dict[int, str] = {}
    for d in eob.get("diagnosis", []):
        try:
            diag[int(d["sequence"])] = d["diagnosisCodeableConcept"]["coding"][0]["code"]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    default_dos = (eob.get("billablePeriod") or {}).get("start")
    lines = []
    for j, item in enumerate(eob.get("item", [])):
        try:
            lines.append(_parse_item(item, j, diag, default_dos))
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
            errors.append(ParseError(f"{path}.item[{j}]", _describe(exc)))
    if not lines:
        errors.append(ParseError(f"{path}.item", "no valid items"))
        return None
    return Claim(
        id=str(eob_id),
        patient_pseudonym=pseudonym(str((eob.get("patient") or {}).get("reference", "unknown"))),
        provider=(eob.get("provider") or {}).get("display"),
        payer=(eob.get("insurer") or {}).get("display"),
        lines=lines,
        source="fhir",
    )


def parse_fhir(data: object) -> ParseResult:
    result = ParseResult()
    if not isinstance(data, dict):
        result.errors.append(ParseError("$", "expected a JSON object"))
        return result
    rtype = data.get("resourceType")
    if rtype == "ExplanationOfBenefit":
        eobs = [("$", data)]
    elif rtype == "Bundle":
        eobs = [
            (f"$.entry[{i}].resource", e["resource"])
            for i, e in enumerate(data.get("entry", []))
            if isinstance(e, dict)
            and isinstance(e.get("resource"), dict)
            and e["resource"].get("resourceType") == "ExplanationOfBenefit"
        ]
    else:
        result.errors.append(
            ParseError("$.resourceType", f"expected Bundle or ExplanationOfBenefit, got {rtype!r}")
        )
        return result
    for path, eob in eobs:
        claim = _parse_eob(eob, path, result.errors)
        if claim is not None:
            result.claims.append(claim)
    return result


def claim_to_eob(claim: Claim) -> dict[str, object]:
    diags = sorted({d for line in claim.lines for d in line.diagnosis_codes})
    dseq = {d: i + 1 for i, d in enumerate(diags)}
    items: list[dict[str, object]] = []
    for i, line in enumerate(claim.lines):
        it: dict[str, object] = {
            "sequence": i + 1,
            "productOrService": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": line.code}]},
            "servicedDate": line.date_of_service.isoformat(),
            "quantity": {"value": line.units},
            "net": {"value": float(line.charge), "currency": "USD"},
        }
        if line.modifiers:
            it["modifier"] = [{"coding": [{"code": m}]} for m in line.modifiers]
        if line.place_of_service:
            it["locationCodeableConcept"] = {"coding": [{"code": line.place_of_service}]}
        if line.diagnosis_codes:
            it["diagnosisSequence"] = [dseq[d] for d in line.diagnosis_codes]
        items.append(it)
    eob: dict[str, object] = {
        "resourceType": "ExplanationOfBenefit",
        "id": claim.id,
        "status": "active",
        "use": "claim",
        "patient": {"reference": f"Patient/{claim.patient_pseudonym}"},
        "diagnosis": [
            {"sequence": dseq[d], "diagnosisCodeableConcept": {"coding": [{"code": d}]}} for d in diags
        ],
        "item": items,
    }
    if claim.provider:
        eob["provider"] = {"display": claim.provider}
    if claim.payer:
        eob["insurer"] = {"display": claim.payer}
    return eob


def claims_to_bundle(claims: list[Claim]) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": claim_to_eob(c)} for c in claims],
    }
