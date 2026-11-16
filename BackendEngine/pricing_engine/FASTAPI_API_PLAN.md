# FastAPI Migration Plan for GAP Pricing

## Purpose

Replace `gap_pricing_gui`'s direct in-process calls to the pricing engine with a versioned HTTP API. Keep pricing, loading, and report-generation logic behind service functions; expose GUI user workflows rather than individual Python helpers. This document is a plan only; it does not add FastAPI or change runtime code.

**Confirmed deployment target:** Linux containers in AWS. The specific AWS compute service and storage/database services still need selection.

## Current GUI workflows and backend calls

| GUI workflow | Current implementation | API-facing operation |
|---|---|---|
| Load basis and assumptions during startup | `gap_pricing_gui/workers.py` calls `pricing_engine.concurrent_runner.load_shared_snapshots` | API startup loads/validates configured snapshots; clients check readiness. Do not make snapshot loading a per-request endpoint. |
| Price a single CTR | `gap_pricing_gui/quote_runner.py:price_quote` calls `ctr_loader.ctr_loader_v2.load_ctr`, loader functions, and pricing functions | Submit a single-quote request and retrieve its job/result. |
| Price a folder of CTRs | `gap_pricing_gui/workers.py` calls `pricing_engine.concurrent_runner.run_quotes_concurrent` | Submit a batch job; retrieve batch status, summary, and individual quote results. |
| Recalculate after GUI overrides | `gap_pricing_gui/main_window.py` applies express-over-GLA, margin, and rate-basis overrides | Submit a validated recalculation request; do not expose `PricingContext` or low-level `price()` directly. |
| Load a historical quote from DB | `quote_runner.list_db_schemes`, `quote_runner.price_quote_from_db` | Optional saved-quote listing/reprice operations, only after replacing the LocalDB integration. |
| Save or view reports | `quote_runner.save_report`, `reporting.write_pricing_report`, `reporting.inputs.gather_cb_data`, `gather_po_data` | Request report generation and retrieve a download link; return structured summary/detail data for GUI grids. |

The GUI currently keeps `CTRBundle`, assumption/basis objects, Polars DataFrames, and batch detail data in process memory. Those Python objects are not API payloads; define explicit JSON response schemas and a persistence strategy before moving the GUI to remote calls.

## Proposed API surface

Prefix endpoints with `/api/v1` and use job-oriented operations for work that may take significant time.

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Process liveness only. |
| `GET /health/ready` | Readiness, including whether required snapshots/configuration are loaded. Do not return local paths or secrets. |
| `POST /api/v1/quotes` | Submit one quote for pricing. Accept an uploaded CTR or an authorized object reference, snapshot identifiers, and validated options. Return a job ID. |
| `POST /api/v1/batches` | Submit a batch of CTR object references and run options. Return a batch/job ID. |
| `GET /api/v1/jobs/{job_id}` | Return queued/running/completed/failed state, progress where available, summary, and safe error details. |
| `GET /api/v1/jobs/{job_id}/quotes/{quote_id}` | Return a quote's scheme metadata, aggregate totals, product summaries, warnings, and result references. Add pagination or product-specific detail routes for large results. |
| `POST /api/v1/quotes/{quote_id}/recalculations` | Reprice using an explicit override payload; retain the original result and identify the new version. |
| `POST /api/v1/quotes/{quote_id}/reports` | Generate requested formats (XLSX, HTML, and/or Parquet) and return a report job/status. |
| `GET /api/v1/reports/{report_id}` | Return report state and a short-lived download URL or authorized file response. |
| `GET /api/v1/saved-quotes` and `POST /api/v1/saved-quotes/{run_id}/reprice` | Optional parity for the GUI's DB lookup/reprice flow. Do not implement until the database target and authentication are decided. |

A synchronous single-quote response can be considered later if measured quote duration is reliably short. Start with the same job/result contract for single quotes and batches so the GUI can poll consistently.

## Service boundary

Create an API-facing service layer (names are illustrative):

- `QuoteService.submit_quote(...)` and `QuoteService.get_result(...)` wrap CTR loading, snapshot selection, `build_context`, and `price_all_products`.
- `BatchService.submit_batch(...)` and `BatchService.get_status(...)` wrap `run_quotes_concurrent` and translate its results into stable response DTOs.
- `OverrideService.recalculate(...)` validates and applies the GUI's supported override types.
- `ReportService.create_report(...)` wraps `write_pricing_report` and returns a report identifier/location, not a local filesystem path.
- Snapshot loading remains an application-lifecycle concern; reuse loaded snapshots where correct and report readiness/version metadata.

Keep `load_ctr`, `get_basis`, `get_assumptions`, `build_context`, `price`, `price_all_products`, `gather_cb_data`, `gather_po_data`, grid extraction, and workbook-sheet helpers internal. The API should return domain data and downloadable artifacts, not Qt grid structures, `CTRBundle` objects, or raw Polars DataFrames.

## Implementation phases

1. **Set API/data decisions.** Keep Linux as the production container target. Select the AWS compute model (for example, ECS Fargate for the API and ECS tasks or AWS Batch for pricing jobs), decide how CTR files and snapshots reach the service (upload, S3 object keys, or mounted storage), which snapshot versions a request may select, and whether saved-quote DB access is required. Define identity/authorization and file-size limits.
2. **Extract service functions and DTOs.** Separate GUI callbacks and in-memory cache assumptions from quote, batch, override, and report operations. Define JSON-safe request/response schemas and error mapping. Preserve current pricing behavior and golden outputs.
3. **Add FastAPI application lifecycle.** Load validated settings and required snapshots at startup; close executors/resources at shutdown. Implement liveness/readiness endpoints and request-scoped logging/correlation IDs.
4. **Implement single-quote and batch jobs.** Add job IDs, status transitions, progress/error summaries, result retrieval, and safe retry/idempotency behavior. Do not rely on a process-local dictionary for production job state if tasks can restart or scale horizontally.
5. **Implement GUI parity.** Add override/recalculation and report-generation operations. Return product summaries and report links; let the GUI render its own grids. Add saved-quote operations only if the database feature is retained.
6. **Migrate the GUI client.** Replace direct calls from `quote_runner.py` and `workers.py` with API calls. Replace local `QuoteResult`/`_batch_cache` dependence with job/result IDs and fetched DTOs. Keep the GUI presentation widgets client-side.
7. **Secure and deploy.** Add authentication/authorization, input validation, upload limits, and protections against arbitrary client filesystem paths. Containerize the API and select an AWS job execution model; keep credentials in AWS-managed secret/config services and use IAM roles.
8. **Verify parity and operations.** Compare single and batch outputs against approved golden cases, test each override and report format, test job failure/retry behavior, and verify readiness, logs, and permissions in the target AWS environment.

## AWS and dependency constraints

- The API must never trust a client-provided local path such as `C:\...` or `/home/...`. Use upload streams, validated S3 keys, or controlled mounted locations.
- The current `LocalS3` implementation is local filesystem storage, not AWS S3. If using S3 directly, add an S3 adapter; add an AWS SDK dependency only after selecting that approach. A mounted storage design has different tradeoffs.
- `pricing_engine/concurrent_runner.py` currently uses SQL Server LocalDB with Windows trusted authentication. This must be replaced with the selected AWS-accessible database/authentication approach before database endpoints or Linux production deployment are considered complete.
- FastAPI and an ASGI server (for example, Uvicorn) are intentional future runtime dependencies for this API. Do not add unrelated libraries; use existing project dependencies where possible and add only dependencies required by selected API/storage/deployment features.
- The current batch runner has asynchronous report/file/database workers and in-memory state. Production job execution and state need explicit lifecycle, durability, and concurrency decisions before horizontal scaling.

## Acceptance criteria

- Every GUI workflow in scope uses documented HTTP operations rather than importing pricing/loader/report modules directly.
- API requests and responses contain JSON-safe schemas; large tabular results are paginated or returned as files.
- Single and batch pricing match approved golden results, including supported overrides.
- CTRs, snapshots, reports, and DB access use configured server-side storage; no client-supplied filesystem path is opened directly.
- Jobs and report operations expose useful state and safe errors; process restarts do not silently lose production job state.
- API startup/readiness, authentication, logs, and target AWS deployment behavior are verified.
