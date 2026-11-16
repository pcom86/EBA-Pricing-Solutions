# GAP Pricing Engine: R1 dead-code cleanup, status and next steps

**Date:** 2026-10-12 | **Owner:** Mohamed Hussein Gadiwala

## 1. Where we are

- **No live engine file has been changed.** Batch A exists only as a DEV copy and has not been promoted.
- **One blocker:** the DEV golden runs came back CLEAN, but they did not test the DEV code. The harness reported `from elsewhere: 31`, so 31 of the 32 engine modules loaded from the live `J:` folder. Only 1 loaded from `GAP_DEV_R1_A`. Those two CLEAN results are not valid evidence for batch A.
- **Cause found in the code:** three places hardcode the live `J:` folder and put it first on `sys.path` (section 5).
- **Next step:** make the DEV run load DEV code, re-run both golden cases, and only then promote.

## 2. What has been done

| # | Item | Result |
|---|---|---|
| 1 | Reviewed the Handover Plan and the Golden-Test Status note | Critical assessment given, including conflicts with your environment (Python 3.10 vs the plan's 3.12) |
| 2 | Built `code_health.py` (read-only audit and hardcoding gate) | In `tools\`. Tested in a sandbox on synthetic folders |
| 3 | Built `settings.py` and `assumption_access.py` | Staged in `tools\_staged\`. **Not wired into the engine.** Tested in a sandbox only |
| 4 | Ran the audit on the live engine | Baseline saved to `tools\_baseline\code_health_20261012_145147.json` |
| 5 | Ran the R1 dead-code diagnostic | 3 batches defined (section 4) |
| 6 | Built the cleanup notebook `GAP_R1_cleanup_v3.ipynb` | Cell A plans and builds a DEV copy. Cell B promotes or rolls back. Neither touches live until `MODE = "promote"` |
| 7 | Built and verified batch A in DEV | Section 6 |
| 8 | Ran golden verification on the DEV copy | Invalid, see section 5 |

## 3. Baseline (live code only, 52 files)

The scan excludes `1Not important`, `local_s3_bucket`, `Archive`, `_backups` and `tools`.

| Measure | Value | Handover Plan figure |
|---|---|---|
| Functions with docstrings | 0 of 602 | 0 of 597 |
| Functions over 80 lines | 27 | 27 |
| Hidden inputs (`getattr` on scheme/ctr) | 88 | 93 |
| Broad `except` / silent | 55 / 12 | not given |
| `sys.path` edits | 11 | about 20 |
| `datetime.utcnow()` | 2 | not given |
| Wildcard imports | 6 (all in `helpers.py`) | not given |
| Unused imports | 59 (my auditor, a lower bound); **85 by ruff (F401)** | about 100 |
| Hardcoded environment values | 10 | not given |

Use ruff's 85 as the authoritative unused-import count.

## 4. R1 dead-code batches

Scope rule set by the owner: the engine uses code only from `ctr_loader`, `gap_pricing_gui`, `pricing_engine` and `reporting`. A function is removed only if nothing inside those folders uses it. Scripts, notebooks and kernel history outside them do not count.

| Batch | What | Size | Status |
|---|---|---|---|
| **A** | `product_aggregates`, `build_membership_block`, `build_normal_margins_block`, `write_engine_sheet`, `scheme_summary`, `category_summary`, `member_summary`, `prefetch_async` | 8 functions, 348 lines | Built in DEV, **not verified** |
| **A2** | Helpers only batch A used, plus `_as_yes`, `_normalise_percent`, `_lookup_spouse_factor` | 14 functions, about 204 lines | Not started. Leaves `aggregation.py` with no code |
| **B** | Loop-based GIP and TIP chain (`_gip_compute_member_rows`, `_gip_build_lookup_tables`, `_tip_compute_member_rows`, and others) plus their `__all__` entries | 16 functions, about 722 lines | Not started |
| C | `engine_bundle.activate`, `engine_bundle.warm_async`, `QuoteResult.summary_text`, `SingleQuoteView.reset` | about 140 lines | Needs a GUI smoke test |

Kept on purpose: `rezip_snapshot` (and its imports in both loaders), `FastModel.headerData` (Qt override), `compare_to_baseline_v2`, `read_xlsx_values`, `_require_columns`.

The live `_apply_gip_composite` and `_apply_tip_composite` do not call any of the batch B code. The real Step 4 targets are those two functions (413 and 464 lines), not the loop-based ones.

## 5. The blocker: the DEV run loads live code

All three locations are in the live code and were printed by the read-only diagnostic.

| File and line | What it does |
|---|---|
| `pricing_engine/concurrent_runner.py:24` | `_CODE_ROOT` is the `J:` path. `_ensure_paths()` (lines 73-79) inserts `J:\...\ctr_loader`, `pricing_engine`, `reporting` and the root at the front of `sys.path` |
| `pricing_engine/engine_paths.py:9-16` | Hardcodes `J:\...\ctr_loader`, inserts it at `sys.path[0]`, then loads `engine_paths.py` from `J:` by file path as `_engine_paths_canonical` |
| `ctr_loader/engine_paths.py:9-10, 18-31` | `_resolve_root()` tries the `GAP_ENGINE_ROOT` environment variable, then `J:`, then the UNC path. `ensure_on_path()` moves `ctr_loader` to the front of `sys.path` |

`_engine_paths_canonical` is the first module in your `elsewhere` list, and the rest are modules imported after those paths were set. The code confirms the mechanism. I have not confirmed which single module came from DEV.

**Candidate fix (not built, not tested):**

1. In the golden subprocess, set `GAP_ENGINE_ROOT` to the DEV folder. `ctr_loader/engine_paths.py` already honours it first.
2. In the DEV copy only, point the two hardcoded `J:` literals (`concurrent_runner.py:24` and `pricing_engine/engine_paths.py:9`) at the DEV folder. Neither file is in a batch manifest, so Cell B would never promote those edits.
3. Cell A should refuse to build if a file it would promote contains a `J:` or UNC literal that differs from live.

**Unknowns to check first:**
- `ENGINE_ROOT` also decides where `snapshot_dir()` looks, and the DEV folder has no snapshot folders. Your harness passes explicit `basis_dir` and `assump_dir`, so the golden path probably doesn't use `snapshot_dir()`. This is unverified.
- Cell A copies only `.py` files into DEV. If the harness needs other files there, the first DEV run will say so.

**Real fix, later:** make the engine root configurable (Handover Plan Step 5, `edge/settings.py`). That changes live code, so it needs its own DEV copy and golden run. It is not part of R1.

## 6. Batch A, built in `C:\Users\x517853\GAP_DEV_R1_A`

52 `.py` files copied, 4 edited: `ctr_cache.py`, `aggregation.py`, `demographics.py`, `meta_engine.py`.

| Measure | Live | DEV |
|---|---|---|
| Functions | 602 | 591 |
| Functions over 80 lines | 27 | 26 |
| Silent excepts | 12 | 11 (the one inside `prefetch_async`) |
| Unused imports | 59 | 59 |
| Never-referenced functions | 16 | 16 (8 removed, 8 new) |

- The 11 fewer functions match the diff: 8 module-level plus 3 nested inside them.
- The 8 newly never-referenced functions are batch A2 targets: `_ensure_cols`, `_basis_key_for_product`, `_compute_aggregates_for_product`, `_empty_column`, `_format_val`, `_resolve_product_labels`, `compute_eoh_row`, `_guess_format`.
- No new unused imports. The diff only removes lines (364 removed, 0 added).

## 7. Rules in force

- **Workflow:** diagnose read-only, back up live, write to a DEV copy, test the DEV copy, promote, re-test live. Never mix a refactor with a behaviour change.
- **Promotion gate:** `verify_golden` must report CLEAN for **both** `fictitious_test_all` and `fictitious_edug_30_07` with `from elsewhere: 0`. A CLEAN result with `from elsewhere` above 0 does not count.
- **Rollback:** Cell B restores the exact original files from `_backups\r1_batch<id>_<key>`, and only if live still matches the promoted state.

## 8. Next steps, in order

1. **Make the DEV run test DEV code** (section 5). Start with a read-only check: run `verify_golden` on the DEV folder with `GAP_ENGINE_ROOT` set, and see how many modules load from DEV.
2. Re-run both golden cases on `GAP_DEV_R1_A`. Both must be CLEAN with `from elsewhere: 0`.
3. Cell B: `dry_run`, then `promote` with both cases listed. Run `verify_golden` on live. If either is not CLEAN, roll back.
4. Repeat for A2, then B.
5. Persist the golden harness as `tests\golden_harness.py` (task H1). It currently lives only in notebook memory, which caused a `NameError` today in a fresh notebook.
6. Then the plan's R2 (remove the engine-to-GUI import) and R4 (silent excepts). Start with `assumption_loader.py:88` and `basis_loader.py:106`, which swallow errors while reading provenance from the build log.

## 9. Decisions needed from you

| Decision | Why it matters |
|---|---|
| Keep the loop-based GIP/TIP code as a test oracle? | Batch B deletes it, and it is the only independent implementation. If yes, extract it to `tests\` first. The backup also keeps a copy |
| Is `promote_loaders.ipynb` still your snapshot or loader promotion route? | If yes, `rezip_snapshot` and its imports stay |
| How to handle the real-client CTRs in `Test\Batch run` | From the earlier status note. They should not be captured until you decide on anonymisation or path-and-hash storage |
| Who is the pricing actuary for the D-number decisions? | D4, D5, D6, D7, D17, D2, D14 and D16 each move numbers and need a decision first |

## 10. Corrections to earlier statements

- I first said the `ctr_loader_v2.py` silent excepts (lines 852, 937, 1040) could hide pricing errors. They wrap `wb.unload_sheet(...)`, which is memory cleanup. The two silent excepts worth fixing are `assumption_loader.py:88` and `basis_loader.py:106`.
- I first listed the `rezip_snapshot` import removal in batch A. It is dropped because `promote_loaders.ipynb` calls `bnew.rezip_snapshot(...)`.
- I first estimated 602 to 594 functions for batch A. The actual figure is 591 because nested functions count.
- The plan's `utcnow()` replacement is not neutral. Use `datetime.now(timezone.utc).replace(tzinfo=None)` where the value goes to SQL Server, and treat it as low priority on Python 3.10.

## 11. Files and locations

| Item | Location |
|---|---|
| Audit tool | `<engine>\tools\code_health.py` |
| Staged, not wired | `<engine>\tools\_staged\` (`settings.py`, `assumption_access.py`, two test files) |
| Baseline counts | `<engine>\tools\_baseline\code_health_20261012_145147.json` |
| Cleanup notebook | `GAP_R1_cleanup_v3.ipynb` (Downloads) |
| Batch A DEV copy | `C:\Users\x517853\GAP_DEV_R1_A` (includes `_r1_batchA.diff` and `_r1_manifest.json`) |
| Backups, created only on promote | `<engine>\_backups\r1_batch<id>_<key>` |
| Golden store | `<engine>\tests\golden\` |
