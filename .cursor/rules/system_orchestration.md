# System Orchestration Profile

This master configuration profile defines the core engineering standards and development principles for the trading and screening platform. All agents and human developers must strictly adhere to these rules.

---

## 1. Spec-Driven & Doubt-Driven Development (from Addy Osmani Agent Skills)

### Spec-Driven Development
Before writing a single line of implementation code, you must design the API contract, types, error signatures, and edge cases.
- **Boundary Contracts**: Explicitly specify the inputs, outputs, and type structures for all boundaries.
- **API Fallback Matrix**: Define a strict multi-tier fallback mechanism when external APIs (e.g., `yfinance`) are down, rate-limited, or return stale/incomplete datasets.

### Doubt-Driven Development
Actively doubt data integrity and system availability:
- **Zero-Division catch blocks**: Never perform any division (especially financial ratio calculations) without a hard guard checking if the denominator is $\le 0$ or NaN.
- **Attribute Scavenging**: Account for missing, NaN, or null database fields or API attributes. Provide fallback calculations (e.g., summing Short Term Debt + Long Term Debt when Total Debt is not present).

---

## 2. OpenAPI Schema Verification & Strict Boundaries (from OpenSpec)
- All incoming and outgoing data at pipeline boundaries (such as API routes, file boundaries, and database states) must be strictly verified against schemas.
- Do not pass open, unvalidated `**kwargs` or generic dictionaries through pipelines.
- Ensure SQLite schema parameters, column constraints (such as `CHECK`), and JSON storage structures match type contracts.
- Database entries must always map to strongly-typed models or checked validations.

---

## 3. The Beyonce Rule & DAMP Testing (from Learn Go With Tests)
- **The Beyonce Rule**: "If you liked it, then you should have put a test on it." Every financial threshold, business logic branch, or mathematical condition must be covered by a unit test.
- **DAMP (Descriptive and Meaningful Phrases)**: Prefer readability over DRY (Don't Repeat Yourself) in the test codebase. Write verbose, self-documenting test names that describe the exact boundary condition.
- **Boundary Parity Tests**: Explicitly test values exactly on the limit, one unit/basis point below the limit, and one unit/basis point above the limit (e.g., test 25.0% vs 25.01% for a 25% debt limit, and 3.0% vs 3.01% for interest income).
