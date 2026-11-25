# GAP Pricing Engine Developer Handover

**Project:** EBA Pricing Solutions / GAP Pricing Engine  
**Branch:** `Mohamed/sql-database-integration`  
**Certified checkpoint:** `2501b69` (`Add reconciled golden pricing regression baseline`)  
**Handover status:** Pricing-engine reconciliation complete and ready for software-development ownership  

## 1. Executive summary

The pricing engine has been reconciled against the legacy Combined Tech Tool for the certified `golden_test.ctr` quote. The reconciled engine output has been promoted to a durable, member-level golden baseline covering all 14 active products.

The routine regression oracle is now the saved engine output, not the Excel Tech Tool. The Tech Tool evidence is retained only as certification provenance. Future engine or SQL changes should be verified by running the automated golden regression test.

The committed checkpoint contains:

- 14 member-level Parquet product baselines;
- run identity, product schemas, row counts, active-row counts and SHA-256 hashes;
- aggregate and per-product numeric totals;
- selected Tech Tool reference values and reconciliation conclusions;
- a reusable automated comparator;
- documentation explaining the baseline and comparison policy.

No pricing-engine source was changed merely to force reconciliation.

## 2. Certified repository checkpoint

| Item | Value |
|---|---|
| Repository | `C:\Dev\EBA-Pricing-Solutions` |
| Backend root | `BackendEngine` |
| Branch | `Mohamed/sql-database-integration` |
| Commit | `2501b69` |
| Commit subject | `Add reconciled golden pricing regression baseline` |
| Committed scope | 19 files exactly |
| Golden products | 14 |
| Automated tests | 16/16 passed |
| Test runtime at certification | 3.51 seconds |

The commit deliberately excludes the pre-existing untracked paths:

- `BackendEngine/_backups/`
- `_golden_test_output/`

## 3. Golden baseline layout

```text
BackendEngine/tests/golden/golden_test/
    README.md
    manifest.json
    product_totals.json
    techtool_reference.json
    products/
        accident_death.parquet
        accident_disability.parquet
        ci.parquet
        dismemberment.parquet
        educate_a_child.parquet
        ext_fc.parquet
        fc.parquet
        gip.parquet
        gla.parquet
        lsdb.parquet
        spouse_gla.parquet
        spouse_lsdb.parquet
        terminal.parquet
        tip.parquet

BackendEngine/tests/test_golden_pricing_baseline.py
```

### Purpose of each artifact

- `products/*.parquet`: primary member-level regression oracle.
- `manifest.json`: run identity, schema, row-count, active-row and hash metadata.
- `product_totals.json`: fast aggregate and numeric-total checkpoint.
- `techtool_reference.json`: legacy Tech Tool certification evidence only.
- `README.md`: intended use and comparison policy.
- `test_golden_pricing_baseline.py`: automated comparator.

## 4. Certified quote and engine reproduction

The certified quote is `golden_test.ctr` with:

- scheme: `Fictitious Test All`;
- quote number: `1`;
- 14 active products;
- 10,000 member rows for each member-based product;
- 30 Ext-FC rate-card rows.

A fresh-kernel execution reproduced the prior engine checkpoint exactly:

| Aggregate | Certified value |
|---|---:|
| Total annual premium | `121,027,839.000707656145096` |
| Total subtotal premium | `121,027,839.000707656145096` |
| Total gross risk premium | `130,104,926.925760731101036` |
| Total cover | `29,987,390,490.298469543457031` |

Important: `total_gross_risk_premium` in `QuoteResult.aggregates` is currently calculated as `total_annual_premium * 1.075`. It is not an independently reconciled sum of every product's Tech Tool gross premium. Product-specific gross reconciliation remains the authoritative gross layer.

## 5. Active products in the golden quote

The active-product order is part of the regression contract:

1. `gla`
2. `lsdb`
3. `ci`
4. `spouse_gla`
5. `spouse_lsdb`
6. `terminal`
7. `accident_death`
8. `accident_disability`
9. `dismemberment`
10. `fc`
11. `ext_fc`
12. `gip`
13. `tip`
14. `educate_a_child`

## 6. Reconciliation completed

### 6.1 GLA-family products

The 10 active GLA-family products are reconciled and represented in the golden baseline:

- GLA
- LSDB
- CI
- Spouse GLA
- Spouse LSDB
- Terminal Illness
- Accidental Death
- Accidental Disability
- Dismemberment
- Educate a Child

### 6.2 Ext-FC

Ext-FC reconciliation is complete across 30 rate-card rows. The golden snapshot contains 30 active rows.

### 6.3 FC

FC is reconciled. Developers should retain the distinction between:

- the engine technical premium/subtotal; and
- the Tech Tool's cross-subsidised and gross reporting layers.

These are different semantic layers and should not be forced into direct equality without reproducing the Tech Tool allocation logic.

### 6.4 GIP

GIP completed a final corrected gate of **97/97 checks**:

| Gate | Result |
|---|---:|
| Direct category boundaries | 24/24 |
| Product components | 9/9 |
| Workbook category identities | 30/30 |
| Category allocations | 18/18 |
| Category-to-scheme roll-ups | 16/16 |

Key GIP mappings:

- Tech net risk equals engine `basic_technical_premium + aids_technical_premium + waiver_premium`.
- Engine `total_annual_premium` adds `co_premium`.
- Profit Refund loading is 3% of net risk.
- Cash for Loved Ones loading is 1.5% of net risk.
- Profit is 10% of net risk.
- Admin/policy fee is 10% of net risk at product level.
- VAT is 15% of commission.
- Category policy fee and commission are workbook allocations, not direct engine category profit fields.
- Workbook category basic, escalation and HCC-waiver reporting rows can be cross-subsidised and must not be compared directly to engine category net risk.

Certified GIP reference values:

| Metric | Value |
|---|---:|
| Active members | `9,490` |
| Engine basic technical premium | `13,956,576.503440540283918` |
| Engine waiver premium | `173,925.478876823821338` |
| Engine conversion-option premium | `334,142.098140723654069` |
| Engine total annual premium | `14,464,644.080458085983992` |
| Tech net risk premium | `14,130,501.982316978275776` |
| Tech gross premium | `18,178,216.556525401771069` |

### 6.5 TIP

TIP completed a final gate of **107/107 checks**:

| Gate | Result |
|---|---:|
| Direct category boundaries | 24/24 |
| Product components | 9/9 |
| Escalation pattern | 6/6 |
| Workbook category identities | 30/30 |
| Category allocations | 18/18 |
| Category-to-scheme roll-ups | 16/16 |
| Commission sheet | 4/4 |

Key TIP mappings:

- Engine `total_annual_premium` equals the engine basic technical premium for this certified run; optional engine premium components are zero.
- Conversion option is zero in both engine and Tech Tool.
- Profit Refund loading is 3%.
- Cash for Loved Ones loading is 1.5%.
- Profit is 10%.
- Admin/policy fee is 10% at product level.
- VAT is 15% of commission.
- Category 5 contains the TIP escalation amount in the certified workbook; the other active categories have zero escalation in that reporting row.

Certified TIP reference values:

| Metric | Value |
|---|---:|
| Active members | `9,454` |
| Engine / Tech net risk | approximately `3,496,787.5371489` |
| Tech gross premium | `4,447,193.205614904` |

## 7. Automated regression test

### Standard isolated command

The repository-level `pytest.ini` currently injects `pytest-cov` arguments. In an environment where `pytest-cov` is not installed or loaded, use `-c NUL` on Windows to avoid that unrelated configuration failure.

```powershell
cd C:\Dev\EBA-Pricing-Solutions
$env:GAP_GOLDEN_CTR = "C:\path\to\golden_test.ctr"
python -m pytest -c NUL BackendEngine/tests/test_golden_pricing_baseline.py -q -ra
```

Expected result:

```text
16 passed
```

The 16 tests consist of:

- 1 run-identity test;
- 1 aggregate test;
- 14 parameterized product-frame tests.

### What the comparator checks

- scheme and quote identity;
- active-product order;
- aggregate keys and values;
- product row counts;
- column order;
- data types and complete schema;
- null placement;
- NaN placement;
- exact equality for non-floating columns;
- tolerant comparison for Float32/Float64 values;
- member/category identifiers in failure diagnostics.

### Numeric tolerance

The test uses:

- absolute tolerance floor: `1e-8`;
- relative tolerance: `1e-11`.

This follows the established `compare_to_baseline_v2` approach and avoids false positives caused by machine-epsilon floating-point differences. Do not replace it with strict floating-point equality.

## 8. How developers should use the golden baseline

For every pricing-engine, CTR-loader, assumption-loader, SQL-persistence or integration change:

1. Make the change in a controlled branch or DEV copy.
2. Run focused unit/integration tests.
3. Run the 16-test golden gate.
4. Treat any golden mismatch as a regression until it is explained.
5. Inspect the reported product, column, row/member identity, expected value, actual value, absolute difference and allowed tolerance.
6. Do not update the golden baseline simply to make tests pass.
7. Update the golden only after an intentional actuarial/methodology change has been independently reviewed and re-certified.

The golden baseline should be immutable during ordinary refactoring, performance work, persistence changes and UI integration.

## 9. Certified CTR distribution is still required

The automated comparator requires the certified `golden_test.ctr` through `GAP_GOLDEN_CTR`.

At certification time it was located at:

```text
C:\Users\x517853\Downloads\golden_test.ctr
```

That personal path must not become the team's permanent dependency. The development team should place the exact certified file in an approved internal artifact location and document its checksum and retrieval process.

Recommended next action:

- publish the certified CTR in the team's controlled SharePoint, build-artifact store or secured test-data repository;
- record a SHA-256 checksum;
- configure local/CI `GAP_GOLDEN_CTR` to point to the retrieved artifact;
- do not commit the CTR to Git unless repository governance explicitly permits a roughly 22 MB test artifact containing this data.

## 10. Work remaining for developers

### 10.1 CI integration

Add the golden regression to the appropriate CI stage. The job must:

- obtain the certified CTR securely;
- set `GAP_GOLDEN_CTR`;
- install/activate the expected test dependencies;
- run `test_golden_pricing_baseline.py`;
- archive useful pytest output on failure;
- avoid silently regenerating the baseline.

The repo's `pytest.ini` currently expects `pytest-cov`. Either install `pytest-cov` in the standard developer/CI environment or keep the isolated `-c NUL` command for this test. Do not leave developers with a command that fails during argument parsing.

### 10.2 SQL persistence integration

The pricing oracle is complete, but SQL persistence/integration remains a separate workstream. Developers should verify that persisted and reconstructed quotes reconcile to the same golden engine output.

Recommended SQL integration gates:

1. price the certified CTR in memory;
2. persist the quote/run/product/member/pricing-result data;
3. reconstruct or query the persisted result;
4. compare persisted totals and member-level rows to the golden baseline;
5. run the 16-test engine gate to prove SQL changes did not change pricing behavior;
6. keep persistence failures separate from pricing failures in diagnostics.

The established SQL schema/persistence work includes modules for stored objects, runs, immutable quote versions, member lineage, member pricing results, Ext-FC rate-card results and transaction orchestration. Protected legacy components were deliberately kept outside earlier persistence changes.

### 10.3 Future Tender Tool identifiers

The following identifiers were intentionally not treated as currently wired Tender Tool fields and should remain nullable/unwired until their upstream source is implemented:

- `scheme_business_key`
- `tender_number`
- `chorus_quote_number`
- `chorus_gapq`
- `external_quote_number`

Do not fabricate or infer these values during persistence.

### 10.4 Reporting and gross-premium semantics

Do not assume every engine subtotal equals a Tech Tool reporting subtotal at category level. The legacy workbook contains:

- cross-subsidisation;
- category allocations;
- escalation presentation rows;
- HCC-waiver reporting rows;
- policy fee allocation;
- commission allocation;
- VAT;
- gross-premium presentation layers.

Where these outputs are needed in the future product/reporting layer, implement explicit named transformations rather than overloading `total_annual_premium`.

### 10.5 Golden-baseline governance

Create a controlled process for intentional baseline replacement. At minimum, a replacement should require:

- documented reason and affected products;
- actuarial approval;
- comparison against the previous golden;
- review of member-level differences;
- updated Tech Tool or replacement actuarial evidence where applicable;
- a dedicated commit containing the baseline change;
- no unrelated code changes in that commit.

## 11. Known cautions and interpretation rules

1. **Do not use aggregate-only testing.** Equal grand totals can hide offsetting member-level errors.
2. **Do not use strict float equality.** Use the comparator tolerances.
3. **Do not interpret invite-like fields or reporting labels as engine semantics.** Confirm the actual formula/field mapping.
4. **Do not treat Tech Tool category allocations as engine category pricing.** GIP demonstrated this clearly for policy fee and commission.
5. **Do not use the Tech Tool for routine regression.** It is historical certification evidence now.
6. **Do not update the golden after a failure without diagnosis and approval.**
7. **Do not stage `BackendEngine/_backups/` or `_golden_test_output/` accidentally.** They were excluded from the certified commit.
8. **Do not rely on the personal Downloads path for team automation.** Publish the certified CTR properly.

## 12. Definition of done for this handover

Pricing-engine validation is considered complete because:

- all 14 active products have durable member-level golden outputs;
- the legacy Tech Tool reconciliation has been completed for the relevant product families and reporting layers;
- GIP passed 97/97 final checks;
- TIP passed 107/107 final checks;
- the fresh engine run reproduced the certified checkpoint;
- all baseline files were round-trip verified and hashed before promotion;
- the automated regression passed 16/16 tests without Excel;
- the baseline and comparator were committed in a strict 19-file commit;
- commit `2501b69` contains no unrelated files.

## 13. Handover conclusion

The actuarial/pricing reconciliation phase is complete and the pricing engine can be handed to software development at commit `2501b69`.

The development team's primary responsibilities are now operationalisation and integration:

- distribute the certified CTR through an approved mechanism;
- add the golden gate to CI;
- complete SQL persistence/reconstruction reconciliation;
- integrate future Tender Tool identifiers when their upstream wiring exists;
- preserve the semantic distinction between engine technical pricing and reporting/gross allocation layers;
- govern any future golden replacement as an intentional actuarial change.

The default expectation for ordinary development is simple:

> **The 16-test golden regression must remain green, and the committed member-level baseline must not change.**
