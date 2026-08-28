"""
DPDPA 2023 retention decision logic.

Deliberately pure: takes facts in, returns a decision out. No DB session,
no side effects. This is what lets the compliance logic itself be unit
tested independent of persistence — the routes/worker do the DB writes.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class RetentionRule:
    basis: str          # citation-friendly statutory tag
    min_days: int        # statutory minimum retention, in days


# Purpose code -> Sec 8(7) retention exception ("another law requires it").
# None purposes have no such exception and are erasure-eligible on withdrawal.
# Verified against primary sources (not just recalled from training) — see
# README "External-law citations" section for the search trail on each one.
RETENTION_RULES: dict[str, Optional[RetentionRule]] = {
    # RBI Master Direction – KYC, 2016, Para 46(b): identity records held 5
    # yrs after the business relationship ends. Verified via RBI's own
    # published Master Direction text.
    "LOAN_UNDERWRITING":   RetentionRule("RBI_MASTER_DIRECTION_KYC_2016_PARA_46", 1825),
    # The 5-year figure comes from the PMLA (Maintenance of Records) Rules,
    # 2005 made under PMLA Sec 12 — not a period stated in the Act itself.
    "AML_KYC":              RetentionRule("PMLA_MAINTENANCE_OF_RECORDS_RULES_2005", 1825),
    # CGST Act 2017, Sec 36: 72 months (6 yrs = 2190 days) from the due date
    # of the annual return — NOT 8 years, and NOT Income Tax Act Sec 44AA
    # (which sets no retention period at all). Earlier version of this file
    # had both the citation and the duration wrong; corrected after checking
    # the actual CGST Act text.
    "ECOMMERCE_INVOICE":    RetentionRule("CGST_ACT_2017_SEC_36", 2190),
    # IT (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021,
    # Rule 3(1)(h): 180 days after registration cancellation/withdrawal.
    # Renamed from "..._CERTIN" — CERT-In's own 2022 Directions impose a
    # DIFFERENT 5-year log-retention duty on a different class of entities
    # (data centres/VPN/cloud providers); conflating the two under one tag
    # was wrong even though the 180-day number itself was already correct.
    "PLATFORM_REG_LOGS":    RetentionRule("IT_INTERMEDIARY_GUIDELINES_2021_RULE_3_1_h", 180),
    # 3 years from commencement of treatment. The NMC's 2023 regulations
    # were held in abeyance days after notification (Aug 2023) and the
    # Indian Medical Council (Professional Conduct, Etiquette and Ethics)
    # Regulations, 2002, Reg 1.3.1 were reinstated and remain the regulation
    # actually in force — it specifies the same 3-year figure, but citing
    # "NMC_REGULATIONS" pointed at the wrong (suspended) instrument.
    "TELEMEDICINE_RECORD":  RetentionRule("MCI_2002_REG_1_3_1", 1095),
    "MARKETING": None,
}

IMMEDIATE_ERASURE_GRACE = timedelta(hours=24)
# NOT a DPDPA Rules citation. Rule 8(2)'s 48hr notice is scoped specifically
# to Rule 8(1) dormancy erasure (see below) — it does not govern ordinary
# withdrawal-triggered erasure. This is an internal operational buffer only.

# --- Rule 8(1)/(2)/(3), DPDP Rules 2025: applies ONLY to the classes listed
# in the Third Schedule — NOT to every Data Fiduciary. For every other
# purpose (loans, KYC, marketing, telemedicine...), Sec 8(7)(a)'s "purpose
# no longer served" duty still exists in principle, but Sec 8(8) requires a
# period to be *prescribed*, and only these three classes currently have one.
THIRD_SCHEDULE_PURPOSES: dict[str, int] = {
    "ECOMMERCE_LARGE_PLATFORM_ACCOUNT": 1095,        # 3 yrs — Third Schedule item 1
    "SOCIAL_MEDIA_LARGE_PLATFORM_ACCOUNT": 1095,     # Third Schedule item 3
    "ONLINE_GAMING_LARGE_PLATFORM_ACCOUNT": 1095,    # Third Schedule item 2
}
# Rule 8(1) counts from "last approached... or the commencement of the DPDP
# Rules 2025, whichever is latest." Rules 5-16 (incl. Rule 8) commence 18
# months after the Rules' Nov 13, 2025 publication.
RULE_8_COMMENCEMENT = datetime(2027, 5, 13, tzinfo=timezone.utc)
RULE_8_NOTICE_WINDOW = timedelta(hours=48)  # Rule 8(2) — the real 48hr citation


@dataclass
class RetentionDecision:
    next_status: str
    retention_exception_basis: Optional[str] = None
    legitimate_use_basis: Optional[str] = None
    erasure_deadline: Optional[datetime] = None
    log_retention_until: Optional[datetime] = None


def evaluate_retention(
    purpose_code: str,
    legal_claim_type: Optional[str] = None,
    now: Optional[datetime] = None,
) -> RetentionDecision:
    """
    Decide the post-withdrawal (or post-dormancy) path for a ConsentRecord
    under DPDPA 2023, Sec 8(7).

    legal_claim_type:
      - "LOAN_DEFAULT" -> Sec 17(1)(f): processing to ascertain financial
        info/assets of a defaulted borrower. The Act's own illustration in
        17(1)(f) is this exact scenario (loan, default, financial info).
      - "OTHER_LEGAL_CLAIM" -> Sec 17(1)(a): the general exemption for
        enforcing any legal right or claim (litigation, arbitration, etc.)
      - None -> no legitimate-use exemption in play.

    - No Sec 8(7) exception + no legal claim -> PENDING_ERASURE (Sec 8(7) default duty)
    - A Sec 8(7) exception applies and/or a Sec 17(1) legitimate-use basis
      exists -> RETAINED_LEGAL_HOLD
    """
    now = now or datetime.now(timezone.utc)
    rule = RETENTION_RULES.get(purpose_code)

    if legal_claim_type is not None and legal_claim_type not in ("LOAN_DEFAULT", "OTHER_LEGAL_CLAIM"):
        # An unrecognized value must never silently produce RETAINED_LEGAL_HOLD
        # with no legitimate_use_basis recorded — that would hold data with
        # zero legal justification, purely because of a bad/misspelled input.
        # The API layer also rejects this via a Pydantic Literal, but this
        # function must refuse to misbehave even called directly.
        raise ValueError(
            f"legal_claim_type={legal_claim_type!r} is not a recognized value "
            "(must be None, 'LOAN_DEFAULT', or 'OTHER_LEGAL_CLAIM')"
        )

    if rule is None and legal_claim_type is None:
        return RetentionDecision(
            next_status="PENDING_ERASURE",
            erasure_deadline=now + IMMEDIATE_ERASURE_GRACE,
        )

    decision = RetentionDecision(next_status="RETAINED_LEGAL_HOLD")
    if rule is not None:
        decision.retention_exception_basis = rule.basis
        decision.log_retention_until = now + timedelta(days=rule.min_days)

    if legal_claim_type == "LOAN_DEFAULT":
        decision.legitimate_use_basis = "LOAN_DEFAULT_FINANCIAL_ASCERTAINMENT_17_1_f"
    elif legal_claim_type == "OTHER_LEGAL_CLAIM":
        decision.legitimate_use_basis = "LEGAL_CLAIM_ENFORCEMENT_17_1_a"

    return decision


def is_third_schedule_purpose(purpose_code: str) -> bool:
    """Only these classes have a Rules-prescribed dormancy period at all."""
    return purpose_code in THIRD_SCHEDULE_PURPOSES


def is_rule8_dormant(purpose_code: str, last_data_principal_contact: datetime, now: Optional[datetime] = None) -> bool:
    """
    Rule 8(1), DPDP Rules 2025: for Third Schedule classes ONLY. The 3-year
    clock runs from "last approached... or the commencement of the DPDP
    Rules 2025, whichever is latest" — so before Rule 8's own commencement
    date, nothing is ever dormant under this rule, by definition.
    Callers must check is_third_schedule_purpose() first; this raises for
    any other purpose rather than silently applying an invented period.
    """
    if purpose_code not in THIRD_SCHEDULE_PURPOSES:
        raise ValueError(
            f"{purpose_code!r} is not in the Third Schedule — Rule 8 dormancy "
            "erasure has no prescribed period for this purpose. Use "
            "flag_stale_for_review() instead."
        )
    now = now or datetime.now(timezone.utc)
    threshold_days = THIRD_SCHEDULE_PURPOSES[purpose_code]

    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    if last_data_principal_contact.tzinfo is not None:
        last_data_principal_contact = last_data_principal_contact.astimezone(timezone.utc).replace(tzinfo=None)
    commencement = RULE_8_COMMENCEMENT.replace(tzinfo=None)

    window_start = max(last_data_principal_contact, commencement)
    return (now - window_start) >= timedelta(days=threshold_days)


# Purely operational (NOT a DPDPA citation): for purposes outside the Third
# Schedule, no erasure period is currently prescribed under the Rules, so
# this never auto-transitions a record. It only flags long-quiet records for
# a human DPO to review — reflecting that the Sec 8(7)(a) duty exists but is
# not yet operationalizable by timer for these purposes.
STALE_REVIEW_THRESHOLD_DAYS = 730


def is_stale_for_review(last_data_principal_contact: datetime, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    if last_data_principal_contact.tzinfo is not None:
        last_data_principal_contact = last_data_principal_contact.astimezone(timezone.utc).replace(tzinfo=None)
    return (now - last_data_principal_contact) >= timedelta(days=STALE_REVIEW_THRESHOLD_DAYS)
