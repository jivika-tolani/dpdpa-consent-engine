# DPDPA Consent Lifecycle & Retention Enforcer

A backend service that automatically manages user consent and data deletion in line with India's Digital Personal Data Protection Act, 2023.

   ![Tests](https://github.com/jivika-tolani/dpdpa-consent-engine/actions/workflows/tests.yml/badge.svg)

## The Problem

Under India's data protection law, a company must delete a person's data as soon as that person withdraws their consent. At the same time, other Indian laws often require companies to keep certain records for a fixed number of years, regardless of what the customer wants.

<<<<<<< HEAD
This creates a direct conflict between two different legal obligations. Handled manually, that conflict is slow, inconsistent, and easy to get wrong, and getting it wrong can mean significant financial penalties.
=======
This creates a direct conflict between two different legal obligations. Handled manually, that conflict is slow, inconsistent and easy to get wrong and getting it wrong can mean significant financial penalties.
>>>>>>> 7719e5382eea59a6cb21904d5bf6ab4957b0048d

## Where This Conflict Shows Up

This is not a problem unique to one industry. The table below shows how it plays out across different types of businesses. The first three rows are the ones this project's code actually implements and has automated tests for; the last two are included to show that the same underlying conflict exists elsewhere, even though this project does not yet handle those specific cases.

| Industry | Example Situation | What Requires the Data to Be Kept | How Long |
|---|---|---|---|
| **Banking / Lending** (implemented) | A customer withdraws consent while they still have an outstanding loan. | RBI's KYC rules and India's anti-money-laundering law (PMLA) | 5 years |
| **E-Commerce** (implemented) | A customer buys a product, receives a GST invoice, and deletes their account. | India's GST law (Central Goods and Services Tax Act, Section 36) | 6 years |
| **Messaging / Social Media** (implemented) | A user cancels their account registration. | IT Rules, 2021 (rules for online platforms) | 180 days |
| **Healthcare / Telemedicine** (implemented) | A patient asks an online doctor's platform to erase their medical records. | Medical Council regulations governing doctors' record-keeping | 3 years |
| **EdTech** (not yet implemented) | A student finishes a certification course and asks for their account to be deleted. | Education regulator record-keeping norms | Varies |
| **Ride-Hailing / Delivery** (not yet implemented) | A user deletes their profile after a disputed ride or payment. | Consumer protection and transport rules | Until the dispute is resolved |

<<<<<<< HEAD
In every one of these cases, the business cannot simply delete the data the moment the customer asks, because doing so would break a different law. This project automates the decision of what to do instead — hold the data securely, or delete it — for the cases it currently supports.

## How It Solves the Problem

The service acts as an automatic decision-maker that sits between "a customer withdrew consent" and "the data is actually deleted." Whenever consent is withdrawn, it checks:

- Does any other law require this data to be kept for a fixed period? If so, the data is locked in a secure, read-only state until that period ends, instead of being deleted.
- Is there no such requirement? Then the data is scheduled for deletion, and it is deleted automatically once the required waiting period passes.
- Has the person simply gone quiet for a very long time without withdrawing consent or using the service? For a small number of large platforms, the law treats this the same as a withdrawal, and the same process applies. For everyone else, the system flags the record for a human reviewer, because the law has not yet set a fixed time period for those cases.

Every decision the system makes — every time a status changes — is written to a permanent, tamper-evident record. If someone tried to alter that history afterward, the system would detect it. This gives the company a clear, defensible paper trail for regulators and auditors.

A human compliance officer can also step in at any time to manually force a deletion or a hold, for example if a customer's loan has been fully repaid ahead of schedule.
=======
In every one of these cases, the business cannot simply delete the data the moment the customer asks, because doing so would break a different law. This project automates the decision of what to do instead, to hold the data securely or to delete it for whatever cases it currently supports.

## How It Solves the Problem

The service acts as an automatic decision-maker that sits between "a customer withdrew consent" and "the data is actually deleted." Whenever consent is withdrawn and checks if:

- Any other law require this data to be kept for a fixed period? If so, the data is locked in a secure, read-only state until that period ends instead of being deleted.
- Is there no such requirement? Then the data is scheduled for deletion, and it is deleted automatically once the required waiting period passes.
- Has the person simply gone quiet for a very long time without withdrawing consent or using the service? For a small number of large platforms, the law treats this the same as a withdrawal and the same process applies. For everyone else, the system flags the record for a human reviewer because the law has not yet set a fixed time period for those cases.

Every decision the system makes, every time a status changes, it is written to a permanent, tamper-evident record. If someone tried to alter that history afterward, the system would detect it. This gives the company a clear, defensible paper trail for regulators and auditors.

A human compliance officer can also step in at any time to manually force a deletion or a hold:- for example if a customer's loan has been fully repaid ahead of schedule.
>>>>>>> 7719e5382eea59a6cb21904d5bf6ab4957b0048d

## How to Use the Code

**What you need first:** Python installed on your computer.

**Step 1 — Install the required components**

```bash
pip install -r requirements.txt
```

**Step 2 — Start the service**

```bash
uvicorn app.main:app --reload
```

**Step 3 — Open the interactive control panel**

Once the service is running, open this address in a web browser: 
```bash
http://127.0.0.1:8000/docs
```


This page lists every action the system can perform and lets you try each one directly from the browser, with no coding required.

**Step 4 — Run the automated checks**

To confirm everything is working correctly:

```bash
pytest -v
```

This runs 46 automated checks covering the core scenarios (consent given, consent withdrawn, legal holds applied correctly) as well as unusual situations (duplicate requests, invalid input, tampered records) to confirm the system behaves correctly in all of them, not just the expected ones.

## What the System Can Do

| Action | What It Means |
|---|---|
| Record a new consent | A customer agrees to their data being used for a stated purpose |
| Withdraw consent | A customer takes back that agreement, triggering the deletion-or-hold decision |
<<<<<<< HEAD
| Check status | See whether a customer's data is active, held, or scheduled for deletion |
| Reactivate | Cancel a pending deletion if the customer re-engages in time |
| Manual override | Allow a compliance officer to force a deletion or a hold, with the reason recorded |
=======
| Check status | See whether a customer's data is active, held or scheduled for deletion |
| Reactivate | Cancel a pending deletion if the customer re-engages in time |
| Manual override | Allow a compliance officer to force a deletion or a hold with the reason recorded |
>>>>>>> 7719e5382eea59a6cb21904d5bf6ab4957b0048d
| Run deletion | Carry out scheduled deletions once their waiting period has passed |
| Verify history | Confirm that a record's history has not been tampered with |

## Honest Limitations

This project does not yet handle sharing a deletion instruction with outside contractors who were given a copy of the data (the law requires this in some cases). It also does not track the exact number of users a company has, which the law uses to decide whether certain large-platform rules apply, this is currently assumed rather than measured. These are documented as known gaps rather than silently ignored and would need to be addressed before this could be used in a real product.
