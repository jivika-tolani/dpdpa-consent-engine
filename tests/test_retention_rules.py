from datetime import datetime, timedelta, timezone

from app.retention_rules import evaluate_retention


def test_marketing_purpose_no_claim_goes_to_pending_erasure():
    d = evaluate_retention("MARKETING")
    assert d.next_status == "PENDING_ERASURE"
    assert d.retention_exception_basis is None
    assert d.legitimate_use_basis is None
    assert d.erasure_deadline is not None


def test_loan_underwriting_goes_to_legal_hold_with_sec_8_7_basis():
    d = evaluate_retention("LOAN_UNDERWRITING")
    assert d.next_status == "RETAINED_LEGAL_HOLD"
    assert d.retention_exception_basis == "RBI_MASTER_DIRECTION_KYC_2016_PARA_46"
    assert d.legitimate_use_basis is None
    assert d.log_retention_until is not None


def test_active_legal_claim_adds_sec_17_1_a_basis_even_without_statute():
    # A non-loan legal claim (litigation etc.) with no dedicated retention
    # statute for the purpose still blocks erasure, under 17(1)(a).
    d = evaluate_retention("SOME_UNMAPPED_PURPOSE", legal_claim_type="OTHER_LEGAL_CLAIM")
    assert d.next_status == "RETAINED_LEGAL_HOLD"
    assert d.retention_exception_basis is None
    assert d.legitimate_use_basis == "LEGAL_CLAIM_ENFORCEMENT_17_1_a"


def test_loan_default_uses_specific_17_1_f_not_generic_17_1_a():
    # Sec 17(1)(f) is the Act's purpose-built clause for defaulted-loan
    # financial ascertainment — distinct from the general 17(1)(a) claim.
    d = evaluate_retention("LOAN_UNDERWRITING", legal_claim_type="LOAN_DEFAULT")
    assert d.legitimate_use_basis == "LOAN_DEFAULT_FINANCIAL_ASCERTAINMENT_17_1_f"


def test_loan_with_active_claim_carries_both_bases():
    d = evaluate_retention("LOAN_UNDERWRITING", legal_claim_type="LOAN_DEFAULT")
    assert d.next_status == "RETAINED_LEGAL_HOLD"
    assert d.retention_exception_basis == "RBI_MASTER_DIRECTION_KYC_2016_PARA_46"
    assert d.legitimate_use_basis == "LOAN_DEFAULT_FINANCIAL_ASCERTAINMENT_17_1_f"


def test_retention_deadline_matches_statutory_minimum_days():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = evaluate_retention("ECOMMERCE_INVOICE", now=now)
    assert d.log_retention_until == now + timedelta(days=2190)


def test_pending_erasure_deadline_is_24h_grace_window():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = evaluate_retention("MARKETING", now=now)
    assert d.erasure_deadline == now + timedelta(hours=24)


def test_dormancy_not_triggered_before_threshold():
    from app.retention_rules import is_rule8_dormant
    # after Rule 8's own commencement, so the commencement floor isn't what's tested here
    now = datetime(2028, 1, 1, tzinfo=timezone.utc)
    last_contact = now - timedelta(days=1000)  # under the 3-year (1095 day) Third Schedule threshold
    assert is_rule8_dormant("ECOMMERCE_LARGE_PLATFORM_ACCOUNT", last_contact, now) is False


def test_dormancy_triggered_after_threshold():
    from app.retention_rules import is_rule8_dormant, RULE_8_COMMENCEMENT
    # 1200 days past commencement — comfortably over the 3-year (1095 day)
    # threshold measured from commencement itself (since last_contact here
    # predates commencement, the window floors at commencement per Rule 8(1)).
    now = RULE_8_COMMENCEMENT + timedelta(days=1200)
    last_contact = RULE_8_COMMENCEMENT - timedelta(days=500)
    assert is_rule8_dormant("ECOMMERCE_LARGE_PLATFORM_ACCOUNT", last_contact, now) is True


def test_dormancy_clock_floored_at_rule_8_commencement():
    from app.retention_rules import is_rule8_dormant
    # Last contact was in 2020, long before Rule 8 existed. Rule 8(1) counts
    # from "last approached... or commencement, whichever is LATEST" — so
    # the clock can't have started before Rule 8 commenced (2027-05-13),
    # regardless of how old last_contact is.
    now = datetime(2027, 6, 1, tzinfo=timezone.utc)  # just after commencement
    last_contact = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert is_rule8_dormant("ECOMMERCE_LARGE_PLATFORM_ACCOUNT", last_contact, now) is False


def test_rule8_dormancy_rejects_non_third_schedule_purpose():
    from app.retention_rules import is_rule8_dormant
    import pytest
    with pytest.raises(ValueError):
        is_rule8_dormant("LOAN_UNDERWRITING", datetime(2020, 1, 1, tzinfo=timezone.utc))


def test_is_third_schedule_purpose():
    from app.retention_rules import is_third_schedule_purpose
    assert is_third_schedule_purpose("SOCIAL_MEDIA_LARGE_PLATFORM_ACCOUNT") is True
    assert is_third_schedule_purpose("LOAN_UNDERWRITING") is False


def test_stale_for_review_not_a_rules_citation_but_still_flags_long_quiet_records():
    from app.retention_rules import is_stale_for_review
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert is_stale_for_review(now - timedelta(days=100), now) is False
    assert is_stale_for_review(now - timedelta(days=800), now) is True
