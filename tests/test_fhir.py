from decimal import Decimal
from typing import Any

from app.ingest.fhir import claim_to_eob, claims_to_bundle, parse_fhir
from tests.helpers import claim, line


def eob(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "resourceType": "ExplanationOfBenefit",
        "id": "EOB1",
        "patient": {"reference": "Patient/abc"},
        "provider": {"display": "Clinic A"},
        "insurer": {"display": "Plan B"},
        "diagnosis": [{"sequence": 1, "diagnosisCodeableConcept": {"coding": [{"code": "E11.9"}]}}],
        "item": items,
        **extra,
    }


def item(seq: int = 1, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sequence": seq,
        "productOrService": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99213"}]},
        "servicedDate": "2026-10-15",
        "quantity": {"value": 1},
        "net": {"value": 150.0, "currency": "USD"},
        "locationCodeableConcept": {"coding": [{"code": "11"}]},
        "diagnosisSequence": [1],
    }
    base.update(over)
    return base


def test_parse_single_eob() -> None:
    res = parse_fhir(eob([item(), item(2, modifier=[{"coding": [{"code": "25"}]}])]))
    assert res.errors == []
    (c,) = res.claims
    assert c.id == "EOB1" and c.provider == "Clinic A" and c.payer == "Plan B"
    assert c.patient_pseudonym.startswith("P-") and "abc" not in c.patient_pseudonym
    assert [x.id for x in c.lines] == ["L1", "L2"]
    assert c.lines[0].charge == Decimal("150.0")
    assert c.lines[0].place_of_service == "11"
    assert c.lines[0].diagnosis_codes == ["E11.9"]
    assert c.lines[1].modifiers == ["25"]


def test_bundle_skips_non_eob_entries() -> None:
    bundle = {
        "resourceType": "Bundle",
        "entry": [{"resource": {"resourceType": "Patient"}}, {"resource": eob([item()])}],
    }
    res = parse_fhir(bundle)
    assert len(res.claims) == 1 and res.errors == []


def test_submitted_adjudication_used_when_net_missing() -> None:
    it = item()
    del it["net"]
    it["adjudication"] = [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": 99.5}}]
    (c,) = parse_fhir(eob([it])).claims
    assert c.lines[0].charge == Decimal("99.5")


def test_period_and_billable_period_fallback_for_date() -> None:
    it = item()
    del it["servicedDate"]
    (c,) = parse_fhir(eob([it], billablePeriod={"start": "2026-10-20"})).claims
    assert c.lines[0].date_of_service.isoformat() == "2026-10-20"


def test_bad_items_reported_with_path_rest_of_claim_kept() -> None:
    res = parse_fhir(
        eob(
            [
                item(1),
                item(2, quantity={"value": 0}),
                item(3, net={"value": -20.0}),
                item(4, productOrService={"coding": []}),
                item(5, quantity={"value": 1.5}),
            ]
        )
    )
    assert [x.id for x in res.claims[0].lines] == ["L1"]
    assert [e.path for e in res.errors] == ["$.item[1]", "$.item[2]", "$.item[3]", "$.item[4]"]


def test_eob_with_no_valid_items_is_an_error() -> None:
    res = parse_fhir(eob([item(1, quantity={"value": 0})]))
    assert res.claims == []
    assert res.errors[-1].path == "$.item" and res.errors[-1].message == "no valid items"


def test_wrong_resource_type_and_non_object() -> None:
    assert parse_fhir({"resourceType": "Patient"}).errors[0].path == "$.resourceType"
    assert parse_fhir([1, 2]).errors[0].path == "$"


def test_missing_id_reported_with_bundle_path() -> None:
    bad = eob([item()])
    del bad["id"]
    res = parse_fhir({"resourceType": "Bundle", "entry": [{"resource": bad}]})
    assert res.errors[0].path == "$.entry[0].resource.id"


def test_round_trip_preserves_audit_fields() -> None:
    original = claim(
        line("L1", modifiers=["25"]), line("L2", code="97110", units=3, charge="91.20", pos="22")
    )
    (back,) = parse_fhir(claim_to_eob(original)).claims
    for a, b in zip(original.lines, back.lines, strict=True):
        assert (a.id, a.code, a.modifiers, a.units, a.charge, a.date_of_service, a.place_of_service) == (
            b.id,
            b.code,
            b.modifiers,
            b.units,
            b.charge,
            b.date_of_service,
            b.place_of_service,
        )
    bundle = claims_to_bundle([original, original])
    assert len(parse_fhir(bundle).claims) == 2


def _bundle(*resources: Any) -> dict[str, Any]:
    return {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}


def test_malformed_inputs_never_raise_and_report_paths() -> None:
    cases: list[tuple[Any, str]] = [
        (eob(None), "$.item"),  # type: ignore[arg-type]
        (eob("x"), "$.item"),  # type: ignore[arg-type]
        (eob([1]), "$.item[0]"),
        (eob([None]), "$.item[0]"),
        (eob([item(net=5)]), "$.item[0]"),
        (eob([item(productOrService={"coding": ["x"]})]), "$.item[0]"),
        (eob([item(quantity={"value": "1e999999999"})]), "$.item[0]"),
        (eob([item(quantity={"value": "Infinity"})]), "$.item[0]"),
        (eob([item(quantity={"value": "NaN"})]), "$.item[0]"),
        (eob([item(quantity={"value": 10001})]), "$.item[0]"),
        (eob([item(modifier=[1])]), "$.item[0]"),
        (eob([item(diagnosisSequence=5)]), "$.item[0]"),
        (eob([item(sequence="x")]), "$.item[0]"),
        (eob([item(net={"value": 1e400})]), "$.item[0]"),
        (eob([item(servicedDate=5)]), "$.item[0]"),
        (_bundle(), "$.entry"),
        ({"resourceType": "Bundle", "entry": None}, "$.entry"),
        ({"resourceType": "Bundle", "entry": "x"}, "$.entry"),
        ({"resourceType": "Bundle", "entry": [5]}, "$.entry[0]"),
        ({"resourceType": "Bundle", "entry": [{"resource": 5}]}, "$.entry[0]"),
        ({"resourceType": "Bundle", "entry": [{}]}, "$.entry[0]"),
    ]
    for data, path in cases:
        res = parse_fhir(data)
        assert path in [e.path for e in res.errors], (data, res.errors)


def test_wrong_typed_claim_level_fields_are_tolerated() -> None:
    res = parse_fhir(
        eob(
            [item()],
            diagnosis=None,
            patient="x",
            billablePeriod="x",
            provider="x",
            insurer={"display": 5},
        )
    )
    assert res.errors == []
    (c,) = res.claims
    assert c.provider is None and c.payer is None and c.lines[0].diagnosis_codes == []


def test_duplicate_sequence_keeps_first() -> None:
    res = parse_fhir(eob([item(1), item(1, net={"value": 5.0})]))
    assert [x.charge for x in res.claims[0].lines] == [Decimal("150.0")]
    assert [(e.path, e.message) for e in res.errors] == [("$.item[1]", "duplicate item sequence 1")]


def test_bundle_with_malformed_entry_still_parses_good_eob() -> None:
    bundle = {
        "resourceType": "Bundle",
        "entry": [5, {"resource": {"resourceType": "Patient"}}, {"resource": eob([item()])}],
    }
    res = parse_fhir(bundle)
    assert len(res.claims) == 1
    assert [e.path for e in res.errors] == ["$.entry[0]"]


def test_serviced_period_start_fallback() -> None:
    it = item()
    del it["servicedDate"]
    it["servicedPeriod"] = {"start": "2026-10-22"}
    (c,) = parse_fhir(eob([it])).claims
    assert c.lines[0].date_of_service.isoformat() == "2026-10-22"
