# AI Agent Plan: AWS Fargate Readiness for EBA Pricing Solutions

## Goal and scope

Make all solution application components except the GUI deployable and verifiably functional on Linux-based AWS ECS Fargate, with containerization as the first priority. The desktop GUI runs separately and communicates with the Fargate backend; it must not perform pricing locally in the target architecture. Preserve pricing behavior and reuse the existing configuration layer. This document is a plan, not an implementation or proof of Fargate compatibility.

The repository contains five cooperating Python packages: `gap_config`, `ctr_loader`, `pricing_engine`, `reporting`, and `gap_pricing_gui`. A pricing job consumes CTR Excel workbooks (`.ctr`) plus basis and assumption Parquet snapshots; it produces XLSX, HTML and/or calculation Parquet reports. SFTP ingestion is not implemented in the inspected code and must not be assumed to exist. An ORM is not required merely to run on Fargate.

**Confirmed boundary:** `gap_config`, `ctr_loader`, `pricing_engine`, and `reporting` support the Fargate backend; `gap_pricing_gui` remains outside Fargate as the user-facing client. This describes execution responsibilities, not a requirement for one container per package. Shared configuration code may also be used by the separate GUI client where appropriate. AWS-managed storage and any external database are dependencies, not application containers required to run on Fargate.

## Current state, verified by source inspection

- Root `Dockerfile` defines a combined PySide6 GUI runtime and an offscreen test target. Its runtime entry point is `python -B -m gap_pricing_gui.launch_gap_gui`; it is not a headless pricing image.
- `pricing_engine/__main__.py` and `pricing_engine/cli.py` provide a separate headless command, `python -B -m pricing_engine`. The CLI loads snapshots and CTR inputs, runs pricing, waits for reports, and returns a nonzero exit code for failures. `--check-config` checks configuration and writable working directories, not full pricing readiness.
- `gap_config/settings.py` already provides validated settings, environment overrides, local cache/staging paths, snapshot selection, optional database settings, and configurable workers. Reuse it rather than introducing a competing configuration system.
- `gap_config/storage.py` supports filesystem and S3 storage; S3 requires the separately declared `requirements-s3.txt` and AWS credentials from the SDK credential chain. The current GUI path validation is mounted-filesystem-only.
- `requirements-engine.txt` declares headless dependencies; `requirements-s3.txt` and `requirements-db.txt` are optional sets. The GUI image installs `gap_pricing_gui/requirements.txt`, not the S3 set. Database integration is disabled by default and requires an appropriate SQL Server ODBC driver and an existing database/schema if enabled.
- `deployments/ECS_LINUX.md` describes a headless ECS-on-EC2 runtime contract, not provisioned Fargate resources. `deployments/GUI_CONTAINER.md` documents the separate GUI image and supplied-display requirement. `pricing_engine/FASTAPI_API_PLAN.md` is a future API plan and contains some stale storage/database statements.
- Unit tests and an opt-in real pricing smoke test exist. `tests/run_container_tests.py` explicitly runs the GUI integration target using mounted fixtures. These tests are useful but do not by themselves prove the headless image or a live Fargate task works.
- No Fargate task definition or infrastructure-as-code deployment was identified in the inspected workspace. Docker image builds, real GUI/pricing workloads and live AWS integration must be verified in the target environment; do not treat static inspection or mocked tests as proof.

## Decisions and prerequisites

The executing agent must discover existing organizational standards and ask the owner when a choice affects architecture, access, cost, or data handling. Do not select AWS resources, accounts, regions, or new Python libraries by guesswork.

1. **Backend and client boundary:** The GUI runs outside Fargate; all non-GUI application execution runs on Fargate. Determine whether the backend needs a long-running API, finite batch tasks, or both, and how the separate GUI authenticates, submits jobs, receives results, and downloads reports. FastAPI is the previously identified API direction; confirm deployment and interface details before implementing it. The current GUI directly imports backend modules, so packaging the backend alone does not achieve this separation.
2. **Data delivery and durability:** Choose S3 prefixes, approved EFS mounts, or a mixture for CTR inputs, snapshots, and outputs. Confirm whether an external SFTP-to-S3 process exists and who owns it. Treat task-local cache/staging as ephemeral, never the only copy of required outputs.
3. **Snapshot reproducibility:** Identify approved basis and assumption versions and a policy for pinning them per job. Avoid relying on modification-time-based latest selection for reproducible production runs.
4. **Job orchestration:** Decide how tasks are started, job status is persisted, duplicate submissions/retries are handled, and failed/partial outputs are reconciled. A one-off ECS task is a possible initial execution model; it does not by itself provide a durable application-level job record or exactly-once processing.
5. **AWS foundation:** Identify account/region, VPC/subnets, outbound connectivity or service endpoints, ECR repository, approved infrastructure-as-code and CI platform, roles, log retention, and encryption/KMS requirements.
6. **Database:** Confirm whether DB-backed quote loading/saving is in scope. If so, select an accessible SQL Server, authentication, existing schema/migrations ownership, TLS and driver. No schema scripts were identified in the earlier repository inventory; verify against current infrastructure before deployment.
7. **Fixtures and baseline:** Obtain approved/sanitized CTRs and compatible snapshots, expected numerical golden results, supported CPU architecture, expected batch size, performance/availability targets, and resource/cost constraints. Do not bake sensitive fixtures into image layers or CI logs.

## Agent execution rules

- Read this plan and current repository instructions; inspect actual files and uncommitted changes before editing. Preserve existing work and do not reset or recreate user-deleted files.
- Keep the implementation focused on the confirmed backend-on-Fargate and separate-GUI boundary. Preserve GUI workflows and CLI behavior; replace direct GUI-to-engine calls with the agreed authenticated client/backend contract and cover parity with tests. Do not add SFTP ingestion, a database ORM, or remote desktop merely because the deployment target is Fargate.
- Use existing dependencies and configuration. Add only dependencies demonstrably required for the selected runtime, such as the already-declared S3 or DB dependency sets when those features are chosen.
- Work from `/app` with source packages present; do not introduce `sys.path` edits or require a developer source bind mount. Keep code/config read-only and use explicitly writable cache/staging.
- Test real data and outputs separately from mocked tests. Report commands, environment, outcomes, and gaps honestly; never claim AWS readiness from a Dockerfile or an offscreen GUI test alone.
- Implement in small reviewable steps with focused tests. Re-run the existing suite and relevant real integration checks. Update deployment docs when runtime behavior changes.

## Implementation phases

Priority is: (1) confirm backend/client contract and data choices, (2) build and run the headless image, (3) establish AWS storage and Fargate infrastructure, (4) connect the separate GUI, and (5) prove end-to-end behavior and operational readiness. The phases below group related work; storage, infrastructure, and failure handling may be developed in parallel once their decisions are approved.

### 1. Establish baseline and freeze decisions

- Inventory current entry points, image targets, dependency sets, current CLI exit/report behavior, configuration, working-tree changes and tests. Verify whether CI/IaC files exist now rather than assuming an inventory remains current.
- Record decisions from the preceding section, approved fixtures, supported architecture, and numerical acceptance baselines. Capture a baseline test result before changes.
- Define the first backend deliverable explicitly: a batch task, API service, or both, and identify the GUI integration path. Keep unresolved choices marked as blockers instead of encoding guesses.

**Done when:** scope, data paths, security responsibilities, and acceptance criteria are recorded; baseline failures and unavailable dependencies are distinguished from regressions.

### 2. Build the production container

- For the selected headless pricing workload, add a dedicated runtime target or Dockerfile that runs `python -B -m pricing_engine` using an exec-form entrypoint. Do not make a GUI display or PySide6 a prerequisite for batch processing. Preserve the existing GUI/test targets.
- Include the required source packages, root configuration, and `requirements-engine.txt`; include `requirements-s3.txt` only when S3 is selected and DB dependencies/native driver only when DB is in scope. Ensure `.dockerignore` allows every manifest and source file actually needed while excluding CTRs, snapshots, credentials, generated reports, and unnecessary files.
- Run as a non-root user. Support a read-only root filesystem with explicitly writable task-local cache/staging or chosen volumes. Never write secrets into the image; use a trusted base image and pinned or controlled dependency sources.
- Select and validate a Fargate-supported OS/CPU architecture and compatible native wheels/libraries. Pin the approved base-image digest for releases; publish immutable image digests to ECR. Document a repeatable build and run command.

**Done when:** the selected production image builds on the target architecture, runs `--check-config` as non-root with the intended filesystem layout, and retains the existing GUI image behavior.

### 3. Make execution and shutdown Fargate-safe

- Exercise real single and multi-CTR jobs through the headless entrypoint. Verify argument/environment precedence, exit status, report modes, concurrent worker limits, stdout/stderr logging, output publication, and no reliance on local developer paths.
- Inspect and implement orderly termination for the selected workload: handle SIGTERM, stop accepting new work where applicable, coordinate active pricing/report/DB workers, and return a clear outcome. Ensure the orchestrator's configured stop timeout and any service health/drain configuration match the chosen workload and Fargate platform limits. A forced kill can still interrupt work; document and test recovery.
- Test partial output, disk exhaustion, read-only/missing mounts, missing snapshots, invalid configuration, interrupted uploads, duplicate job IDs, and shutdown during active work. Ensure retries cannot silently present incomplete or mismatched artifacts as completed.
- Size Fargate CPU, memory, and ephemeral storage from representative measurements of snapshots, CTR staging, report generation and concurrency; avoid committing guessed production values. Select supported Fargate task CPU/memory combinations and ephemeral storage settings for the chosen platform version.

**Done when:** a representative job succeeds and failure/termination cases produce understandable nonzero or explicitly tracked outcomes without silently losing required outputs.

### 4. Integrate selected AWS storage and optional database

- If S3 is selected, validate real task-role access using least-privilege bucket/prefix permissions and any needed KMS rights; test listing, download, publishing, failures, and durable report availability. Keep credentials out of code, images, logs, and test fixtures.
- If EFS is selected, configure approved access points, permissions and encryption; test non-root access, persistence and concurrent writes. Validate which Fargate mounts and filesystem behaviors are actually supported; do not assume Docker-host bind mounts from the EC2 document work on Fargate.
- If DB integration is selected, add only the approved driver/dependencies, inject secrets from the approved AWS secret service, verify TLS/network/auth/schema, and test successful writes, rollback/failure and reconstruction. Do not enable the feature just to make the container start.
- Decide how external SFTP delivery is authenticated, triggered and monitored only if it is in scope; implement it as a separately agreed component or integration, not an inferred input already provided by this repo.

**Done when:** selected inputs, snapshots, outputs, and optional DB work through real AWS identities and storage, and artifacts remain accessible after the task exits.

### 5. Define and deploy Fargate resources

- In the organization's approved IaC system, define an ECS Fargate task definition with `awsvpc`, the selected image digest, supported runtime platform and resources, non-root/read-only settings where feasible, explicit writable mounts, configuration/secrets, logging, and stop timeout. Verify all fields against the selected Fargate platform; do not copy host-only settings from Docker examples.
- Configure a separate execution role for image pulls, logging, and secret injection as required, and a least-privilege task role for application AWS calls. Configure ECR, CloudWatch Logs, VPC/subnets/security groups, networking/endpoints and any KMS permissions according to the agreed design.
- Implement the agreed task launcher/orchestrator and durable job-status mechanism. A batch task should not be modeled as a perpetually healthy service by default. If a long-running API is selected, separately design health/readiness endpoints, routing, authentication, request limits, lifecycle, durable jobs and horizontal scaling.
- Keep the interactive GUI outside Fargate. Deploy the agreed backend interface and validate network access and authentication from the separately hosted GUI.

**Done when:** a reproducible deployment in a nonproduction AWS environment launches the intended workload, uses the intended roles/network/storage, and leaves observable task status and durable outputs.

### 6. Connect the separate GUI to the Fargate backend

- Inventory direct GUI imports and in-process uses of loaders, pricing, reporting, snapshots and database functions. Map single and batch pricing, result/detail views, supported overrides, report downloads, and optional saved-quote workflows to the agreed backend operations.
- Design and implement the chosen authenticated interface and JSON-safe result contracts; the previously planned FastAPI approach is a candidate subject to the backend deployment decisions above. Do not send arbitrary client filesystem paths to the backend: define approved upload or object-reference and artifact-download workflows.
- Migrate GUI callbacks and worker threads to the backend client while retaining GUI presentation locally. Replace assumptions about shared Python objects and process-local caches with explicit job/result identifiers and serialized data. The GUI must not invoke the pricing engine or report generation locally in the target deployment.
- Test real client-to-backend interactions, failures, permissions and representative numerical/workflow parity. Preserve the existing GUI until the replacement paths pass; coordinate rollout and document client setup.

**Done when:** the separately deployed GUI completes the agreed workflows against the Fargate backend, with no local pricing or report generation, and parity is demonstrated with approved fixtures.

### 7. CI, release, and operational readiness

- Add a CI pipeline or target using existing tooling to run unit tests, build the selected Linux image, scan it, run non-root/read-only container checks, and run required real pricing tests with explicitly provided approved fixtures. Keep mocked and real integration suites distinguishable. Make missing required fixtures a failure for the release integration target.
- Add an AWS integration stage that launches a real Fargate task and checks exit status, logs, durable outputs, IAM denials, retry/termination behavior, and cleanup. Publish test and scan evidence without exposing sensitive data.
- Verify pricing parity against approved numerical golden cases, including representative single/batch inputs, relevant product/override scenarios and report formats. Compare values, not just file existence.
- Define image promotion/rollback, log retention and redaction, alerting, task failure investigation, secret rotation, staging/ephemeral storage limits, output lifecycle, snapshot versioning, and cost monitoring. Record any intentionally deferred work.

**Done when:** a documented release candidate passes the agreed local/container/AWS/golden gates and operations can deploy, observe, recover, and roll it back.

## Validation and acceptance checklist

- [ ] Existing dependency-light tests pass; pre-existing failures are identified.
- [ ] Dedicated workload image builds for the approved Linux architecture and executes non-root with intended writable paths.
- [ ] Approved single and batch pricing run inside that image; numerical golden comparisons pass and requested reports are readable.
- [ ] Selected S3/EFS and optional DB integrations work with real AWS credentials/roles and persist outputs after exit.
- [ ] SIGTERM, forced stop, retries, failed reports/uploads and duplicate submissions have tested, documented outcomes; no false success.
- [ ] A real Fargate task completes in a nonproduction account with expected logs, exit status, security configuration, and durable artifacts.
- [ ] CI builds/scans/promotes immutable images and fails when required integration prerequisites are missing.
- [ ] The separately running GUI accesses Fargate backend functionality through the agreed authenticated interface; pricing and reports run on the backend, and representative GUI workflows retain parity.
- [ ] GUI display and client deployment are verified separately; offscreen testing is not claimed as interactive GUI verification.
- [ ] Runbook, resource sizing evidence, security review, known limits, and rollback instructions are approved.

## Handoff deliverables

The executing agent should provide: updated image definition(s) and build instructions; automated tests; chosen IaC and CI changes; deployment/runbook documentation; test evidence including actual environment and image digest; and a short list of any blockers or unverified production behaviors. Update `ECS_LINUX.md`, `GUI_CONTAINER.md`, and `FASTAPI_API_PLAN.md` only where their statements become inaccurate; clearly distinguish ECS-on-EC2, Fargate batch, GUI, and future API scopes.
