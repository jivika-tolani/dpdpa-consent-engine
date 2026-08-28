"""
Stress tests: duplicate/conflicting requests, boundary conditions, invalid
input, tamper detection, and idempotency — as opposed to test_api.py's
single-happy-path-per-endpoint coverage.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import models


def test_duplicate_grant_for_same_principal_and_purpose_is_rejected(client):
    r1 = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_dup", "notice_version": "v1.0"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_dup", "notice_version": "v1.0"})
    assert r2.status_code == 409
    assert r1.json()["record_id"] in r2.json()["detail"]


def test_grant_allowed_again_after_withdrawal_of_prior_consent(client):
    r1 = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_re", "notice_version": "v1.0"})
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_re", "purpose_code": "MARKETING"})
    r2 = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_re", "notice_version": "v1.0"})
    assert r2.status_code == 200
    assert r2.json()["record_id"] != r1.json()["record_id"]
    status = client.get("/api/v1/data/status", params={"data_principal_id": "usr_re", "purpose_code": "MARKETING"}).json()
    assert status["status"] == "ACTIVE"
    assert status["record_id"] == r2.json()["record_id"]


def test_grant_rejects_inactive_notice_same_as_missing(client, db_session):
    db_session.add(models.ConsentNotice(
        version="v1.0-retired", purpose_code="MARKETING", purpose_description="old", is_active=False,
    ))
    db_session.commit()
    r = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_x", "notice_version": "v1.0-retired"})
    assert r.status_code == 404


def test_double_withdraw_second_call_gets_404_not_a_crash(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_dw", "notice_version": "v1.0"})
    r1 = client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_dw", "purpose_code": "MARKETING"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_dw", "purpose_code": "MARKETING"})
    assert r2.status_code == 404  # no ACTIVE record left to withdraw — not a 500, not a silent no-op


def test_withdraw_for_never_granted_principal_returns_404_not_500(client):
    r = client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_never_existed", "purpose_code": "MARKETING"})
    assert r.status_code == 404


def test_data_status_for_unknown_principal_returns_404_not_500(client):
    r = client.get("/api/v1/data/status", params={"data_principal_id": "usr_ghost", "purpose_code": "MARKETING"})
    assert r.status_code == 404


def test_grant_with_malformed_json_returns_422_not_500(client):
    r = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_bad"})  # missing notice_version
    assert r.status_code == 422


def test_withdraw_with_wrong_types_returns_422_not_500(client):
    r = client.post("/api/v1/consent/withdraw", json={"data_principal_id": 12345, "purpose_code": None})
    assert r.status_code == 422


def test_dpo_override_unknown_record_id_returns_404(client):
    r = client.post("/api/v1/admin/dpo-override", json={
        "record_id": "does-not-exist", "action": "FORCE_ERASE", "reason": "test",
    })
    assert r.status_code == 404


def test_dpo_override_invalid_action_returns_400(client):
    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_badaction", "notice_version": "v1.0"})
    record_id = grant_resp.json()["record_id"]
    r = client.post("/api/v1/admin/dpo-override", json={
        "record_id": record_id, "action": "DELETE_EVERYTHING", "reason": "test",
    })
    assert r.status_code == 400


def test_dpo_force_erase_is_idempotent_on_already_erased_record(client):
    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_reerase", "notice_version": "v1.0"})
    record_id = grant_resp.json()["record_id"]
    r1 = client.post("/api/v1/admin/dpo-override", json={"record_id": record_id, "action": "FORCE_ERASE", "reason": "first"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/admin/dpo-override", json={"record_id": record_id, "action": "FORCE_ERASE", "reason": "second"})
    assert r2.status_code == 200  # must not crash on a no-op transition
    assert r2.json()["new_status"] == "ERASED"


def test_erasure_sweep_boundary_deadline_exactly_now_is_erased(client, db_session):
    """erasure_deadline <= now — verify the boundary is inclusive, not off-by-one."""
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_boundary", "notice_version": "v1.0"})
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_boundary", "purpose_code": "MARKETING"})

    record = db_session.query(models.ConsentRecord).filter(
        models.ConsentRecord.data_principal_id == "usr_boundary"
    ).first()
    exact_now = datetime.now(timezone.utc)
    record.erasure_deadline = exact_now
    db_session.commit()

    # sweep runs at some instant >= exact_now (time only moves forward)
    r = client.post("/api/v1/worker/run-erasure-sweep")
    assert record.id in r.json()["erased_record_ids"]


def test_erasure_sweep_never_touches_records_missing_a_deadline(client, db_session):
    """A record stuck ACTIVE or RETAINED_LEGAL_HOLD has erasure_deadline=None
    — the sweep's query must not accidentally match NULL <= now() as true."""
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_nodeadline", "notice_version": "v1.0-loan"})
    client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_nodeadline", "purpose_code": "LOAN_UNDERWRITING",
    })  # goes to RETAINED_LEGAL_HOLD — erasure_deadline stays None

    r = client.post("/api/v1/worker/run-erasure-sweep")
    ids = r.json()["erased_record_ids"]
    status = client.get("/api/v1/data/status", params={
        "data_principal_id": "usr_nodeadline", "purpose_code": "LOAN_UNDERWRITING",
    }).json()
    assert status["record_id"] not in ids
    assert status["status"] == "RETAINED_LEGAL_HOLD"


def test_audit_chain_detects_tampering(client, db_session):
    """The whole point of hash-chaining is to catch a directly-edited row —
    verify it actually does, not just that an untouched chain verifies True."""
    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_tamper", "notice_version": "v1.0"})
    record_id = grant_resp.json()["record_id"]
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_tamper", "purpose_code": "MARKETING"})

    assert client.get(f"/api/v1/audit/{record_id}/verify").json()["chain_valid"] is True

    # simulate someone editing history directly in the DB
    entry = db_session.query(models.ConsentAuditLedger).filter(
        models.ConsentAuditLedger.record_id == record_id
    ).first()
    entry.new_state = "ERASED"  # tamper: claim it was erased when it wasn't
    db_session.commit()

    assert client.get(f"/api/v1/audit/{record_id}/verify").json()["chain_valid"] is False


def test_audit_verify_on_nonexistent_record_is_vacuously_valid(client):
    """No entries to tamper with -> the chain walk finds nothing broken.
    Documenting this rather than assuming it: an empty chain is NOT the
    same guarantee as a real, populated, verified chain."""
    r = client.get("/api/v1/audit/does-not-exist/verify")
    assert r.status_code == 200
    assert r.json()["chain_valid"] is True


def test_stale_review_sweep_does_not_flag_third_schedule_purposes(client, db_session):
    """Third Schedule purposes are handled by the statutory sweep only —
    the stale-review sweep must explicitly skip them, not double-flag."""
    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_skip", "notice_version": "v1.0-ecommerce"})
    record_id = grant_resp.json()["record_id"]
    record = db_session.query(models.ConsentRecord).filter(models.ConsentRecord.id == record_id).first()
    record.last_data_principal_contact = datetime.now(timezone.utc) - timedelta(days=5000)
    db_session.commit()

    r = client.post("/api/v1/worker/run-stale-review-sweep")
    assert record_id not in r.json()["record_ids"]


def test_withdraw_with_unmapped_purpose_code_still_resolves_to_pending_erasure(client, db_session):
    """A purpose with no RETENTION_RULES entry at all (not just MARKETING)
    must fall through to PENDING_ERASURE, not raise a KeyError."""
    db_session.add(models.ConsentNotice(
        version="v1.0-custom", purpose_code="SOME_BRAND_NEW_PURPOSE", purpose_description="x",
    ))
    db_session.commit()
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_custom", "notice_version": "v1.0-custom"})
    r = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_custom", "purpose_code": "SOME_BRAND_NEW_PURPOSE",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "PENDING_ERASURE"


def test_withdraw_rejects_unrecognized_legal_claim_type(client):
    """Regression test for a real bug found via manual fuzzing: a typo'd
    legal_claim_type (e.g. 'MADE_UP_NONSENSE') used to silently produce
    RETAINED_LEGAL_HOLD with legitimate_use_basis=None — holding data with
    NO recorded legal justification. Must be rejected at the API boundary."""
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_typo", "notice_version": "v1.0"})
    r = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_typo", "purpose_code": "MARKETING", "legal_claim_type": "MADE_UP_NONSENSE",
    })
    assert r.status_code == 422
    # and the consent record must be untouched — still ACTIVE, not silently held
    status = client.get("/api/v1/data/status", params={"data_principal_id": "usr_typo", "purpose_code": "MARKETING"}).json()
    assert status["status"] == "ACTIVE"


def test_evaluate_retention_raises_on_unrecognized_legal_claim_type_even_called_directly():
    """The pure function itself must refuse bad input, not just the API
    schema layer — defense in depth for compliance-critical logic."""
    from app.retention_rules import evaluate_retention
    with pytest.raises(ValueError):
        evaluate_retention("MARKETING", legal_claim_type="TOTALLY_MADE_UP")
