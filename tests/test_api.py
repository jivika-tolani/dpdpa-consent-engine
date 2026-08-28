def test_grant_consent_creates_active_record(client):
    r = client.post("/api/v1/consent/grant", json={
        "data_principal_id": "usr_alice",
        "notice_version": "v1.0",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ACTIVE"
    assert body["data_principal_id"] == "usr_alice"


def test_grant_consent_unknown_notice_returns_404(client):
    r = client.post("/api/v1/consent/grant", json={
        "data_principal_id": "usr_alice",
        "notice_version": "v99-does-not-exist",
    })
    assert r.status_code == 404


def test_withdraw_marketing_consent_goes_to_pending_erasure(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_bob", "notice_version": "v1.0"})
    r = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_bob",
        "purpose_code": "MARKETING",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "PENDING_ERASURE"
    assert r.json()["erasure_deadline"] is not None


def test_withdraw_loan_consent_goes_to_legal_hold(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_carol", "notice_version": "v1.0-loan"})
    r = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_carol",
        "purpose_code": "LOAN_UNDERWRITING",
        "legal_claim_type": "LOAN_DEFAULT",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "RETAINED_LEGAL_HOLD"
    assert body["retention_exception_basis"] == "RBI_MASTER_DIRECTION_KYC_2016_PARA_46"
    assert body["legitimate_use_basis"] == "LOAN_DEFAULT_FINANCIAL_ASCERTAINMENT_17_1_f"


def test_withdraw_with_no_active_consent_returns_404(client):
    r = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_ghost",
        "purpose_code": "MARKETING",
    })
    assert r.status_code == 404


def test_data_status_endpoint_returns_latest_record(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_dave", "notice_version": "v1.0"})
    r = client.get("/api/v1/data/status", params={"data_principal_id": "usr_dave", "purpose_code": "MARKETING"})
    assert r.status_code == 200
    assert r.json()["status"] == "ACTIVE"


def test_erasure_sweep_skips_records_before_deadline(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_erin", "notice_version": "v1.0"})
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_erin", "purpose_code": "MARKETING"})

    # deadline is +24h from now, so an immediate sweep should erase nothing
    r = client.post("/api/v1/worker/run-erasure-sweep")
    assert r.status_code == 200
    assert r.json()["erased_count"] == 0


def test_reactivation_blocks_future_erasure(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_frank", "notice_version": "v1.0"})
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_frank", "purpose_code": "MARKETING"})

    r = client.post("/api/v1/consent/reactivate", params={
        "data_principal_id": "usr_frank", "purpose_code": "MARKETING",
    })
    assert r.status_code == 200
    assert r.json()["reactivated"] is True


def test_dpo_force_erase_overrides_legal_hold(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_grace", "notice_version": "v1.0-loan"})
    withdraw_resp = client.post("/api/v1/consent/withdraw", json={
        "data_principal_id": "usr_grace",
        "purpose_code": "LOAN_UNDERWRITING",
    })
    record_id = withdraw_resp.json()["record_id"]

    r = client.post("/api/v1/admin/dpo-override", json={
        "record_id": record_id,
        "action": "FORCE_ERASE",
        "reason": "Manual DPO review — customer satisfied loan in full",
    })
    assert r.status_code == 200
    assert r.json()["new_status"] == "ERASED"


def test_audit_chain_is_valid_after_full_lifecycle(client):
    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_hank", "notice_version": "v1.0"})
    record_id = grant_resp.json()["record_id"]
    client.post("/api/v1/consent/withdraw", json={"data_principal_id": "usr_hank", "purpose_code": "MARKETING"})

    r = client.get(f"/api/v1/audit/{record_id}/verify")
    assert r.status_code == 200
    assert r.json()["chain_valid"] is True


def test_dormancy_sweep_ignores_recently_active_records(client):
    client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_ivy", "notice_version": "v1.0-ecommerce"})
    r = client.post("/api/v1/worker/run-dormancy-sweep")
    assert r.status_code == 200
    assert r.json()["transitioned_count"] == 0


def test_dormancy_sweep_ignores_non_third_schedule_purposes(client):
    # LOAN_UNDERWRITING is not in the Third Schedule — even a very stale
    # record must never be auto-transitioned by the statutory sweep.
    from datetime import datetime, timedelta, timezone
    from app import models

    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_kate", "notice_version": "v1.0-loan"})
    record_id = grant_resp.json()["record_id"]

    db = client.db_session_factory()
    record = db.query(models.ConsentRecord).filter(models.ConsentRecord.id == record_id).first()
    record.last_data_principal_contact = datetime.now(timezone.utc) - timedelta(days=5000)
    db.commit()
    db.close()

    r = client.post("/api/v1/worker/run-dormancy-sweep")
    assert r.status_code == 200
    assert record_id not in r.json()["record_ids"]

    status = client.get("/api/v1/data/status", params={
        "data_principal_id": "usr_kate", "purpose_code": "LOAN_UNDERWRITING",
    }).json()
    assert status["status"] == "ACTIVE"  # untouched by the statutory sweep


def test_dormancy_sweep_transitions_nothing_before_rule_8_commencement(client):
    # Today (this test's run date) is before Rule 8's 2027-05-13 commencement.
    # Rule 8(1) counts from "last approached... or commencement, whichever is
    # LATEST" — so nothing can be dormant under this rule yet, no matter how
    # stale the record, until real time actually reaches that date.
    from datetime import datetime, timedelta, timezone
    from app import models

    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_jack", "notice_version": "v1.0-ecommerce"})
    record_id = grant_resp.json()["record_id"]

    db = client.db_session_factory()
    record = db.query(models.ConsentRecord).filter(models.ConsentRecord.id == record_id).first()
    record.last_data_principal_contact = datetime.now(timezone.utc) - timedelta(days=5000)
    db.commit()
    db.close()

    r = client.post("/api/v1/worker/run-dormancy-sweep")
    assert r.status_code == 200
    assert record_id not in r.json()["record_ids"]  # correct: Rule 8 hasn't commenced yet


def test_stale_review_sweep_flags_without_changing_status(client):
    from datetime import datetime, timedelta, timezone
    from app import models

    grant_resp = client.post("/api/v1/consent/grant", json={"data_principal_id": "usr_liam", "notice_version": "v1.0-loan"})
    record_id = grant_resp.json()["record_id"]

    db = client.db_session_factory()
    record = db.query(models.ConsentRecord).filter(models.ConsentRecord.id == record_id).first()
    record.last_data_principal_contact = datetime.now(timezone.utc) - timedelta(days=800)
    db.commit()
    db.close()

    r = client.post("/api/v1/worker/run-stale-review-sweep")
    assert r.status_code == 200
    assert record_id in r.json()["record_ids"]

    # flagged for review, but status must NOT auto-change — no prescribed period exists
    status = client.get("/api/v1/data/status", params={
        "data_principal_id": "usr_liam", "purpose_code": "LOAN_UNDERWRITING",
    }).json()
    assert status["status"] == "ACTIVE"
