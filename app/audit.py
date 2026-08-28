"""
Tamper-evident audit logging. Each entry's hash incorporates the previous
entry's hash for the same record, so altering row N breaks the hash of
every row after it — a verifier can walk the chain and detect edits.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ConsentAuditLedger


def _compute_hash(record_id: str, sequence_no: int, prev_hash: str, new_state: str, ts: datetime) -> str:
    # SQLite drops tzinfo on round-trip (aware datetime in, naive datetime
    # back out on read), so isoformat() would produce a different string at
    # write time vs verify time and every hash would "fail" spuriously.
    # Normalize to a naive UTC, microsecond-explicit string on both sides.
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    canonical_ts = ts.strftime("%Y-%m-%dT%H:%M:%S.%f")
    payload = f"{record_id}|{sequence_no}|{prev_hash}|{new_state}|{canonical_ts}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_audit_entry(
    db: Session,
    record_id: str,
    previous_state: str,
    new_state: str,
    action_by: str,
    reason: str | None = None,
) -> ConsentAuditLedger:
    last = (
        db.query(ConsentAuditLedger)
        .filter(ConsentAuditLedger.record_id == record_id)
        .order_by(ConsentAuditLedger.sequence_no.desc())
        .first()
    )
    sequence_no = (last.sequence_no + 1) if last else 1
    prev_hash = last.audit_proof_hash if last else ""
    ts = datetime.now(timezone.utc)

    entry = ConsentAuditLedger(
        record_id=record_id,
        sequence_no=sequence_no,
        previous_state=previous_state,
        new_state=new_state,
        action_by=action_by,
        reason=reason,
        prev_hash=prev_hash,
        audit_proof_hash=_compute_hash(record_id, sequence_no, prev_hash, new_state, ts),
        created_at=ts,
    )
    db.add(entry)
    return entry


def verify_chain(db: Session, record_id: str) -> bool:
    """Walk the ledger for a record and confirm no entry was tampered with."""
    entries = (
        db.query(ConsentAuditLedger)
        .filter(ConsentAuditLedger.record_id == record_id)
        .order_by(ConsentAuditLedger.sequence_no.asc())
        .all()
    )
    prev_hash = ""
    for e in entries:
        expected = _compute_hash(e.record_id, e.sequence_no, prev_hash, e.new_state, e.created_at)
        if expected != e.audit_proof_hash:
            return False
        prev_hash = e.audit_proof_hash
    return True
