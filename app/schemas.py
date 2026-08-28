from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict


class GrantConsentRequest(BaseModel):
    data_principal_id: str
    notice_version: str


class WithdrawConsentRequest(BaseModel):
    data_principal_id: str
    purpose_code: str
    # Sec 17(1)(f) vs 17(1)(a) are distinct legitimate-use exemptions —
    # see app/retention_rules.py evaluate_retention() docstring. Constrained
    # to a real enum: an unrecognized string here must never silently
    # produce a legal hold with no recorded basis (see
    # test_edge_cases.py::test_withdraw_rejects_unrecognized_legal_claim_type).
    legal_claim_type: Optional[Literal["LOAN_DEFAULT", "OTHER_LEGAL_CLAIM"]] = None


class DPOOverrideRequest(BaseModel):
    record_id: str
    action: str  # "FORCE_ERASE" | "FORCE_HOLD"
    reason: str


class ConsentStatusResponse(BaseModel):
    record_id: str
    data_principal_id: str
    status: str
    retention_exception_basis: Optional[str] = None
    legitimate_use_basis: Optional[str] = None
    erasure_deadline: Optional[datetime] = None
    log_retention_until: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
