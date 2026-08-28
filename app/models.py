"""
DPDPA 2023 consent lifecycle schema.

Legal citations embedded as comments so the mapping from code to statute
is auditable at a glance — this is what a DPO or auditor will actually
check first.
"""
from datetime import datetime, timezone
import enum
import uuid
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Text, Boolean, Integer
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConsentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"
    RETAINED_LEGAL_HOLD = "RETAINED_LEGAL_HOLD"
    PENDING_ERASURE = "PENDING_ERASURE"
    ERASED = "ERASED"


class ConsentNotice(Base):
    """The specific notice version presented to the Data Principal — Act Sec 5 (Notice), Rules 2025 Rule 3."""
    __tablename__ = "consent_notices"

    id = Column(String(36), primary_key=True, default=_uuid)
    version = Column(String(50), nullable=False, unique=True)
    purpose_code = Column(String(100), nullable=False)
    purpose_description = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class ConsentRecord(Base):
    """Real-time consent + retention state of a Data Principal for one purpose."""
    __tablename__ = "consent_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    data_principal_id = Column(String(255), nullable=False, index=True)
    notice_id = Column(String(36), ForeignKey("consent_notices.id"), nullable=False)
    status = Column(Enum(ConsentStatus), default=ConsentStatus.ACTIVE, nullable=False)

    ip_address_hash = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    consent_timestamp = Column(DateTime, default=utcnow)
    withdrawal_timestamp = Column(DateTime, nullable=True)

    # Sec 8(7) — exception to the erasure duty (a *different* law requires retention)
    retention_exception_basis = Column(String(255), nullable=True)

    # Sec 17(1) — legitimate-use exemption from needing consent. Two distinct
    # bases: 17(1)(f) for defaulted-loan financial ascertainment (the Act's
    # own illustration matches this scenario), 17(1)(a) for other legal
    # claims (litigation, arbitration). Not the same clause — kept distinct
    # in the tag value (see retention_rules.evaluate_retention).
    legitimate_use_basis = Column(String(255), nullable=True)

    # Sec 8(7)(a) / 8(8) / 8(11) — dormancy: erasure also triggers if the
    # Data Principal neither approaches the fiduciary nor exercises a right
    # for the prescribed period, independent of explicit withdrawal.
    last_data_principal_contact = Column(DateTime, nullable=True)

    # Rule 8(2) applies only to the Third Schedule dormancy flow (see
    # retention_rules.py) — for ordinary withdrawal this is an internal
    # operational timestamp, not itself a Rules citation.
    erasure_deadline = Column(DateTime, nullable=True)
    erasure_notice_sent_at = Column(DateTime, nullable=True)
    user_confirmed_or_reactivated = Column(Boolean, default=False)

    # Sec 8(7) — how long the OTHER law (RBI/PMLA/GST/etc.) requires this
    # record to be kept. NOT Rule 8(3): that rule's 1-year log-retention
    # minimum is scoped to Seventh Schedule (State/security/SDF-assessment)
    # purposes specifically and isn't modeled here — see README.
    log_retention_until = Column(DateTime, nullable=True)

    erasure_audit_hash = Column(String(64), nullable=True)

    notice = relationship("ConsentNotice")


class ConsentAuditLedger(Base):
    """Append-only, hash-chained log for DPB inspections and external audit."""
    __tablename__ = "consent_audit_ledger"

    id = Column(String(36), primary_key=True, default=_uuid)
    record_id = Column(String(36), ForeignKey("consent_records.id"), nullable=False)
    sequence_no = Column(Integer, nullable=False)  # per-record ordering, for chain verification
    previous_state = Column(String(50), nullable=False)
    new_state = Column(String(50), nullable=False)
    action_by = Column(String(255), nullable=False)  # "DATA_PRINCIPAL" | "SYSTEM_CRON" | "DPO"
    reason = Column(String(255), nullable=True)
    prev_hash = Column(String(64), nullable=True)
    audit_proof_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utcnow)
