from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app import models, schemas, audit
from app.retention_rules import evaluate_retention, is_third_schedule_purpose, is_rule8_dormant, is_stale_for_review

Base.metadata.create_all(bind=engine)

app = FastAPI(title="DPDPA 2023 Consent Lifecycle & Retention Enforcer")


@app.post("/api/v1/consent/grant", response_model=schemas.ConsentStatusResponse)
def grant_consent(payload: schemas.GrantConsentRequest, db: Session = Depends(get_db)):
    notice = (
        db.query(models.ConsentNotice)
        .filter(models.ConsentNotice.version == payload.notice_version, models.ConsentNotice.is_active.is_(True))
        .first()
    )
    if not notice:
        raise HTTPException(status_code=404, detail="No active notice found for that version")

    existing_active = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(
            models.ConsentRecord.data_principal_id == payload.data_principal_id,
            models.ConsentNotice.purpose_code == notice.purpose_code,
            models.ConsentRecord.status == models.ConsentStatus.ACTIVE,
        )
        .first()
    )
    if existing_active:
        # Without this check, a second grant for the same principal+purpose
        # creates a duplicate ACTIVE row. Withdrawal then only ever reaches
        # ONE of the duplicates (even picking the most recent, the other
        # stays silently ACTIVE) — a user who withdraws consent could still
        # have their data processed under the stale duplicate. Reject
        # instead of allowing the ambiguity to exist in the first place.
        raise HTTPException(
            status_code=409,
            detail=f"Active consent already exists for this principal and purpose (record_id={existing_active.id})",
        )

    record = models.ConsentRecord(
        data_principal_id=payload.data_principal_id,
        notice_id=notice.id,
        status=models.ConsentStatus.ACTIVE,
        last_data_principal_contact=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()  # assign record.id before it's referenced by the audit entry
    audit.append_audit_entry(db, record.id, "NONE", models.ConsentStatus.ACTIVE.value, "DATA_PRINCIPAL")
    db.commit()
    db.refresh(record)
    return _to_status_response(record)


@app.post("/api/v1/consent/withdraw", response_model=schemas.ConsentStatusResponse)
def withdraw_consent(payload: schemas.WithdrawConsentRequest, db: Session = Depends(get_db)):
    record = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(
            models.ConsentRecord.data_principal_id == payload.data_principal_id,
            models.ConsentNotice.purpose_code == payload.purpose_code,
            models.ConsentRecord.status == models.ConsentStatus.ACTIVE,
        )
        # If the same principal granted the same purpose more than once
        # without withdrawing in between (duplicate opt-in), withdrawal must
        # hit the MOST RECENT active record — matching /data/status's
        # semantics — not an arbitrary row. Withdrawing a stale duplicate
        # while the live one stays ACTIVE would mean a user who explicitly
        # withdrew still sees their data being processed.
        .order_by(models.ConsentRecord.consent_timestamp.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="No active consent record found for that purpose")

    old_status = record.status.value
    decision = evaluate_retention(payload.purpose_code, payload.legal_claim_type)

    record.withdrawal_timestamp = datetime.now(timezone.utc)
    record.status = models.ConsentStatus(decision.next_status)
    record.retention_exception_basis = decision.retention_exception_basis
    record.legitimate_use_basis = decision.legitimate_use_basis
    record.log_retention_until = decision.log_retention_until

    if decision.next_status == "PENDING_ERASURE":
        record.erasure_deadline = decision.erasure_deadline
        # NOT Rule 8(2) — that notice is scoped to Rule 8(1) dormancy erasure
        # only. This is an internal operational timestamp for the ordinary
        # withdrawal-triggered erasure grace window.
        record.erasure_notice_sent_at = datetime.now(timezone.utc)

    audit.append_audit_entry(
        db, record.id, old_status, record.status.value, "DATA_PRINCIPAL",
        reason=decision.retention_exception_basis or decision.legitimate_use_basis or "NO_STATUTORY_HOLD",
    )
    db.commit()
    db.refresh(record)
    return _to_status_response(record)


@app.get("/api/v1/data/status", response_model=schemas.ConsentStatusResponse)
def data_status(data_principal_id: str, purpose_code: str, db: Session = Depends(get_db)):
    record = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(
            models.ConsentRecord.data_principal_id == data_principal_id,
            models.ConsentNotice.purpose_code == purpose_code,
        )
        .order_by(models.ConsentRecord.consent_timestamp.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="No consent record found")
    # Sec 8(8)(b): exercising a right (here, the Sec 11 right to access
    # information about her personal data) resets the dormancy clock. NOT
    # Sec 8(11) — that sub-section defines "not approached" for the
    # separate "performance of the specified purpose" prong of 8(8)(a);
    # checking status is the rights-exercise prong, 8(8)(b), instead.
    record.last_data_principal_contact = datetime.now(timezone.utc)
    db.commit()
    return _to_status_response(record)


@app.post("/api/v1/consent/reactivate")
def reactivate(data_principal_id: str, purpose_code: str, db: Session = Depends(get_db)):
    """
    For Third Schedule purposes this is the Rule 8(2) erasure-abort path.
    For other purposes it aborts the ordinary withdrawal-triggered erasure
    grace window (an operational courtesy, not itself a Rules citation).
    """
    record = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(
            models.ConsentRecord.data_principal_id == data_principal_id,
            models.ConsentNotice.purpose_code == purpose_code,
            models.ConsentRecord.status == models.ConsentStatus.PENDING_ERASURE,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="No pending-erasure record found")
    record.user_confirmed_or_reactivated = True
    db.commit()
    return {"record_id": record.id, "reactivated": True}


@app.post("/api/v1/admin/dpo-override")
def dpo_override(payload: schemas.DPOOverrideRequest, db: Session = Depends(get_db)):
    record = db.query(models.ConsentRecord).filter(models.ConsentRecord.id == payload.record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    old_status = record.status.value
    if payload.action == "FORCE_ERASE":
        record.status = models.ConsentStatus.ERASED
        record.data_principal_id = f"ERASED-{record.id[:8]}"
    elif payload.action == "FORCE_HOLD":
        record.status = models.ConsentStatus.RETAINED_LEGAL_HOLD
        record.retention_exception_basis = record.retention_exception_basis or "DPO_MANUAL_HOLD"
    else:
        raise HTTPException(status_code=400, detail="action must be FORCE_ERASE or FORCE_HOLD")

    audit.append_audit_entry(db, record.id, old_status, record.status.value, "DPO", reason=payload.reason)
    db.commit()
    return {"record_id": record.id, "new_status": record.status.value}


@app.post("/api/v1/worker/run-erasure-sweep")
def run_erasure_sweep(db: Session = Depends(get_db)):
    """
    Manually-triggered version of the background cron for demo/test purposes.
    Erases only records whose 48hr notice window has passed AND who were not
    reactivated by the user in that window.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(models.ConsentRecord)
        .filter(
            models.ConsentRecord.status == models.ConsentStatus.PENDING_ERASURE,
            models.ConsentRecord.erasure_deadline <= now,
            models.ConsentRecord.user_confirmed_or_reactivated.is_(False),
        )
        .all()
    )
    erased_ids = []
    for record in candidates:
        old_status = record.status.value
        record.status = models.ConsentStatus.ERASED
        # anonymize PII fields; retain the row itself for statutory log-retention purposes
        record.data_principal_id = f"ERASED-{record.id[:8]}"
        record.ip_address_hash = None
        record.user_agent = None
        entry = audit.append_audit_entry(db, record.id, old_status, record.status.value, "SYSTEM_CRON")
        record.erasure_audit_hash = entry.audit_proof_hash
        erased_ids.append(record.id)
    db.commit()
    return {"erased_count": len(erased_ids), "erased_record_ids": erased_ids}


@app.get("/api/v1/audit/{record_id}/verify")
def verify_audit_chain(record_id: str, db: Session = Depends(get_db)):
    return {"record_id": record_id, "chain_valid": audit.verify_chain(db, record_id)}


@app.post("/api/v1/worker/run-dormancy-sweep")
def run_dormancy_sweep(db: Session = Depends(get_db)):
    """
    Rule 8(1)/(2), DPDP Rules 2025 — statutory dormancy erasure. Applies
    ONLY to Third Schedule classes (large e-commerce/social-media/gaming
    platforms) — the only Data Fiduciaries with a Rules-prescribed dormancy
    period so far. Every other purpose is handled by run_stale_review_sweep
    below, which does NOT auto-transition status, because no period has
    been prescribed for it yet.
    """
    now = datetime.now(timezone.utc)
    active_records = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(models.ConsentRecord.status == models.ConsentStatus.ACTIVE)
        .all()
    )
    transitioned = []
    for record in active_records:
        purpose_code = record.notice.purpose_code
        if not is_third_schedule_purpose(purpose_code):
            continue
        last_contact = record.last_data_principal_contact or record.consent_timestamp
        if not is_rule8_dormant(purpose_code, last_contact, now):
            continue

        old_status = record.status.value
        decision = evaluate_retention(purpose_code, legal_claim_type=None)

        record.status = models.ConsentStatus(decision.next_status)
        record.retention_exception_basis = decision.retention_exception_basis
        record.log_retention_until = decision.log_retention_until
        if decision.next_status == "PENDING_ERASURE":
            record.erasure_deadline = decision.erasure_deadline
            record.erasure_notice_sent_at = now  # this one IS Rule 8(2)

        audit.append_audit_entry(
            db, record.id, old_status, record.status.value, "SYSTEM_CRON",
            reason="RULE_8_1_THIRD_SCHEDULE_DORMANCY",
        )
        transitioned.append(record.id)

    db.commit()
    return {"transitioned_count": len(transitioned), "record_ids": transitioned}


@app.post("/api/v1/worker/run-stale-review-sweep")
def run_stale_review_sweep(db: Session = Depends(get_db)):
    """
    NOT a DPDPA Rules citation. Sec 8(7)(a) says erasure also triggers when
    a purpose is "no longer being served", but Sec 8(8) requires a period
    to be *prescribed* for that to apply, and only Third Schedule classes
    have one so far. For every other purpose, this sweep flags long-quiet
    ACTIVE records for manual DPO review instead of auto-transitioning them
    — the duty is real, but the timer isn't legally operationalized yet.
    """
    now = datetime.now(timezone.utc)
    active_records = (
        db.query(models.ConsentRecord)
        .join(models.ConsentNotice)
        .filter(models.ConsentRecord.status == models.ConsentStatus.ACTIVE)
        .all()
    )
    flagged = []
    for record in active_records:
        purpose_code = record.notice.purpose_code
        if is_third_schedule_purpose(purpose_code):
            continue  # handled by the statutory sweep instead
        last_contact = record.last_data_principal_contact or record.consent_timestamp
        if not is_stale_for_review(last_contact, now):
            continue

        audit.append_audit_entry(
            db, record.id, record.status.value, record.status.value, "SYSTEM_CRON",
            reason="STALE_NO_PRESCRIBED_PERIOD_FLAG_FOR_DPO_REVIEW",
        )
        flagged.append(record.id)

    db.commit()
    return {"flagged_count": len(flagged), "record_ids": flagged}


def _to_status_response(record: models.ConsentRecord) -> schemas.ConsentStatusResponse:
    return schemas.ConsentStatusResponse(
        record_id=record.id,
        data_principal_id=record.data_principal_id,
        status=record.status.value,
        retention_exception_basis=record.retention_exception_basis,
        legitimate_use_basis=record.legitimate_use_basis,
        erasure_deadline=record.erasure_deadline,
        log_retention_until=record.log_retention_until,
    )
