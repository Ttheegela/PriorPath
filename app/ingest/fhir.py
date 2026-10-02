from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
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


def _obj(v: object) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: object) -> list[Any]:
    return v if isinstance(v, list) else []


def _display(v: object) -> str | None:
    d = _obj(v).get("display")
    return d if isinstance(d, str) else None


def _charge(item: dict[str, Any]) -> Decimal:
    net = _obj(item.get("net")).get("value")
    if net is None:
        for adj in _list(item.get("adjudication")):
            codings = _list(_obj(_obj(adj).get("category")).get("coding"))
            if "submitted" in {_obj(c).get("code") for c in codings}:
                net = _obj(_obj(adj).get("amount")).get("value")
                break
    if net is None:
        raise ValueError("no charge: item.net and submitted adjudication missing")
    charge = Decimal(str(net))
    if not charge.is_finite() or abs(charge) > 10_000_000:
        raise ValueError("charge out of range")
    return charge


def _units(item: dict[str, Any]) -> int:
    q = Decimal(str(_obj(item.get("quantity")).get("value", 1)))
    if not q.is_finite() or abs(q) > 10_000:
        raise ValueError("quantity out of range")
    if q != q.to_integral_value():
        raise ValueError(f"quantity must be a whole number, got {q}")
    return int(q)


def _sequence(raw: object) -> int:
    try:
        if isinstance(raw, bool):
            raise ValueError
        d = Decimal(str(raw))
        if not d.is_finite() or d != d.to_integral_value():
            raise ValueError
        return int(d)
    except (ValueError, ArithmeticError):
        raise ValueError("sequence must be a whole number") from None


def _parse_item(item: object, index: int, diag: dict[int, str], default_dos: str | None) -> LineItem:
    if not isinstance(item, dict):
        raise ValueError("expected an object")
    seq = _sequence(item.get("sequence", index + 1))
    codings = [c for c in _list(_obj(item.get("productOrService")).get("coding")) if isinstance(c, dict)]
    if not codings:
        raise ValueError("no productOrService coding")
    coding = next(
        (c for c in codings if any(s in str(c.get("system", "")).lower() for s in ("cpt", "hcpcs"))),
        codings[0],
    )
    dos_raw = item.get("servicedDate") or _obj(item.get("servicedPeriod")).get("start") or default_dos
    if not dos_raw:
        raise ValueError("no date of service")
    loc = item.get("locationCodeableConcept")
    return LineItem(
        id=f"L{seq}",
        code=coding["code"],
        modifiers=[m["coding"][0]["code"] for m in _list(item.get("modifier"))],
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
    if len(str(eob_id)) > 128:
        errors.append(ParseError(f"{path}.id", "id longer than 128 characters"))
        return None
    diag: dict[int, str] = {}
    for d in _list(eob.get("diagnosis")):
        try:
            diag[int(d["sequence"])] = d["diagnosisCodeableConcept"]["coding"][0]["code"]
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            continue
    default_dos = _obj(eob.get("billablePeriod")).get("start")
    raw_items = eob.get("item", [])
    if not isinstance(raw_items, list):
        errors.append(ParseError(f"{path}.item", "expected a list"))
        return None
    lines: list[LineItem] = []
    seen: set[str] = set()
    for j, item in enumerate(raw_items):
        try:
            line = _parse_item(item, j, diag, default_dos)
        except (KeyError, IndexError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
            errors.append(ParseError(f"{path}.item[{j}]", _describe(exc)))
            continue
        if line.id in seen:
            errors.append(ParseError(f"{path}.item[{j}]", f"duplicate item sequence {line.id[1:]}"))
            continue
        seen.add(line.id)
        lines.append(line)
    if not lines:
        errors.append(ParseError(f"{path}.item", "no valid items"))
        return None
    try:
        return Claim(
            id=str(eob_id),
            patient_pseudonym=pseudonym(str(_obj(eob.get("patient")).get("reference", "unknown"))),
            provider=_display(eob.get("provider")),
            payer=_display(eob.get("insurer")),
            lines=lines,
            source="fhir",
        )
    except Exception as exc:  # noqa: BLE001 - trust boundary: never raise on user JSON
        errors.append(ParseError(path, _describe(exc)))
        return None


def parse_fhir(data: object) -> ParseResult:
    result = ParseResult()
    if not isinstance(data, dict):
        result.errors.append(ParseError("$", "expected a JSON object"))
        return result
    rtype = data.get("resourceType")
    eobs: list[tuple[str, dict[str, Any]]] = []
    if rtype == "ExplanationOfBenefit":
        eobs = [("$", data)]
    elif rtype == "Bundle":
        entries = data.get("entry")
        if not isinstance(entries, list):
            result.errors.append(ParseError("$.entry", "expected a list"))
            return result
        for i, e in enumerate(entries):
            res = e.get("resource") if isinstance(e, dict) else None
            if not isinstance(res, dict):
                result.errors.append(ParseError(f"$.entry[{i}]", "expected an object with a resource"))
            elif res.get("resourceType") == "ExplanationOfBenefit":
                eobs.append((f"$.entry[{i}].resource", res))
        if not eobs:
            result.errors.append(ParseError("$.entry", "no ExplanationOfBenefit resources"))
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
