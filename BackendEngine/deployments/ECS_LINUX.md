# GAP pricing: Linux Docker on ECS on EC2

## Runtime contract

The container runs a **headless batch job**, not the PySide6 desktop GUI:

```sh
python -B -m pricing_engine
```

Run from the repository root copied into the image (for example, `/app`). Use an exec-form container entrypoint such as `["python", "-B", "-m", "pricing_engine"]`. Keep application packages and the default `config.json` read-only. Provide separate writable cache/staging directories and persistent outputs. Do not mount a developer source tree over the image or configure Python import paths.

This document describes the application runtime contract. It does not provision an ECS cluster, task definition, container image, network, IAM policy, or database. The image and live AWS integration must be validated separately.

## Build and check the headless image

The Dockerfile and `.dockerignore` are in `BackendEngine`. From the solution root,
build the headless image with the `BackendEngine` directory as its context:

```sh
docker build -f BackendEngine/Dockerfile --target headless -t gap-pricing:local BackendEngine
```

For workloads using S3, select the optional target, which installs the existing
`requirements-s3.txt` manifest:

```sh
docker build -f BackendEngine/Dockerfile --target headless-s3 -t gap-pricing:s3 BackendEngine
```

The image entrypoint is `python -B -m pricing_engine`. A non-root, read-only
runtime configuration check can be run without pricing fixtures:

```sh
docker run --rm --init --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  gap-pricing:local --check-config
```

This verifies configuration and writable cache/staging paths only; it is not a
representative pricing job or proof of Fargate readiness.

## Dependencies

Use an approved existing Python environment or build these declared dependency sets into the image:

- `requirements-engine.txt`: headless pricing and reporting, using versions already declared by the GUI.
- `requirements-s3.txt`: boto3, required only when any input, snapshot, output, or database artifact uses S3.
- `requirements-db.txt`: optional pyodbc. The image also needs a compatible native SQL Server ODBC driver and its OS prerequisites.
- `gap_pricing_gui/requirements.txt`: desktop GUI workload, not required by the headless command.

No dependency is installed automatically by the headless entrypoint. The Windows desktop launcher has separate legacy dependency-install behavior; do not use it as the container entrypoint.

## Configuration precedence

1. Explicit per-run CLI options, where available.
2. `GAP_*` environment overrides.
3. The selected JSON file.
4. Safe defaults for omitted optional settings.

`--config` selects a JSON file ahead of `GAP_CONFIG_FILE`. Otherwise the loader finds root `config.json` relative to the application package, not the current directory. Relative paths inside JSON **and environment path overrides** are resolved against the selected JSON file's directory. Explicit CLI paths are relative to the process working directory. Use absolute paths in production.

The loader is cached per process. Configure the task before startup; changing the JSON while a task runs is not a supported hot-reload mechanism. Unknown JSON sections/settings are rejected.

| JSON setting | Environment override | Meaning |
|---|---|---|
| `paths.input` | `GAP_INPUT_DIR` | CTR directory or S3 prefix |
| `paths.output` | `GAP_OUTPUT_DIR` | Report directory or S3 prefix |
| `paths.cache` | `GAP_CTR_CACHE_DIR` | Local cache; null defaults to platform temporary storage |
| `paths.staging` | `GAP_STAGING_DIR` | Local staging; null defaults to platform temporary storage |
| `paths.artifacts` | `GAP_ARTIFACT_ROOT` | Filesystem/S3 store for optional database artifacts |
| `paths.basis` | `GAP_BASIS_ROOT` | Root containing versioned basis snapshot folders/prefixes |
| `paths.assumptions` | `GAP_ASSUMPTION_ROOT` | Root containing versioned assumption snapshot folders/prefixes |
| `snapshots.basis` | `GAP_BASIS_SNAPSHOT` | Single version directory name |
| `snapshots.assumptions` | `GAP_ASSUMPTION_SNAPSHOT` | Single version directory name |
| `database.enabled` | `GAP_DB_ENABLED` | Boolean; defaults to false |
| Environment only | `GAP_DB_CONNECTION_STRING` | Injected secret; required when DB loading is enabled |
| `workers.pricing` | `GAP_PRICING_WORKERS` | Positive integer; default 6 |
| `workers.reports` | `GAP_REPORT_WORKERS` | Positive integer; default 3 for background GUI report work |
| `workers.database` | `GAP_DB_WORKERS` | Positive integer; default 1 for background GUI DB work |
| `service_urls.<name>` | `GAP_SERVICE_<NAME>_URL` | Nonsecret HTTP(S) base URL; names are lowercase identifiers in JSON |

Headless report/DB work is awaited within pricing workers; it does not use the GUI background worker limits. Service URLs are available to consumers through `get_settings().service_urls`; this does not introduce an HTTP API/client or fabricate service endpoints.

`GAP_ENGINE_ROOT` is a backward-compatible **filesystem data base** for relative settings only. It no longer changes code discovery. Prefer explicit per-location overrides; S3 URIs are not accepted for this legacy variable. There are no implicit `J:`, UNC, OneDrive, or Windows LocalDB fallbacks.

Pin snapshot versions for reproducible production runs. If a version is null/omitted, the latest nonempty snapshot is selected by local folder modification time or newest direct Parquet object modification time on S3. Each selected snapshot must contain direct `*.parquet` files. `--basis-dir` and `--assumption-dir` specify an exact version directory/prefix, not its parent root.

## Mounted filesystem deployment

Mount immutable CTR/snapshot inputs read-only, and output storage read-write. Bind mounts or EFS are suitable according to the deployment's persistence/access requirements. Configure task environment values such as:

```text
GAP_INPUT_DIR=/data/ctrs
GAP_OUTPUT_DIR=/data/output
GAP_BASIS_ROOT=/data/basis_snapshots
GAP_ASSUMPTION_ROOT=/data/assumption_snapshots
GAP_CTR_CACHE_DIR=/tmp/gap/cache
GAP_STAGING_DIR=/tmp/gap/staging
GAP_DB_ENABLED=false
```

The example repository input directory contains test fixtures. Production must override it. The container user needs read permission on inputs/snapshots, write permission on cache/staging, and write permission on mounted outputs. Outputs written only to a task's writable layer are not durable.

For a read-only root filesystem, provide a writable `/tmp` or explicit writable cache/staging mount. Provision enough task/host disk for all staged inputs, snapshots, and reports. No cache/log file needs to be written beside application code.

## S3 deployment

Any combination of local and S3 locations is supported:

```text
GAP_INPUT_DIR=s3://input-bucket/ctrs/job-123
GAP_OUTPUT_DIR=s3://output-bucket/pricing/job-123
GAP_BASIS_ROOT=s3://snapshot-bucket/basis
GAP_ASSUMPTION_ROOT=s3://snapshot-bucket/assumptions
GAP_ARTIFACT_ROOT=s3://artifact-bucket/pricing
AWS_DEFAULT_REGION=<region>
```

Assign an **ECS task IAM role** (`taskRoleArn`) to the task. boto3 uses its default credential chain; no access key or secret key is accepted in application JSON or passed explicitly by this code. Do not inject static AWS credentials. The task execution role is separate and does not replace the application task role.

Grant only required bucket/prefix permissions: `s3:ListBucket` scoped to input/snapshot prefixes, `s3:GetObject` on those objects, and `s3:PutObject` on output/artifact prefixes. Optional database artifact deduplication also needs object read/head permission. SSE-KMS buckets need the corresponding KMS permissions. Configure networking through approved S3 endpoints or outbound access, and ensure task-role credential endpoint access is not blocked.

Existing parsers consume local files. The adapter downloads S3 prefixes into task-local staging, handles paginated listings, and downloads individual objects atomically. Staged inputs/snapshots remain in the task-local staging area for its lifetime; use one bounded batch job per task and discard that temporary storage on termination. Long-lived processes require a deliberate staging-retention policy.

The CLI prices direct `.ctr` files in the input directory (case-insensitive suffix); it does not recursively price nested input folders. S3 directory staging preserves nested objects, but they are not automatically selected as CTR jobs. Use `--ctr-file` for explicitly selected objects. Supply actual S3 object keys, not pre-signed HTTP URLs. S3 locations are not processed with `pathlib.Path`.

Report publication is **not a multi-object transaction**. A failed upload can leave some files under the output prefix. Exit code 1 means the run must not be treated as fully published; reconcile partial outputs before retrying. A retry generates new report names. The adapter rejects traversal/drive keys and ambiguous S3 authority/path syntax.

## Optional database loading

DB loading is disabled by default and requires both `GAP_DB_ENABLED=true` and an injected `GAP_DB_CONNECTION_STRING`. Use ECS secrets injection backed by Secrets Manager or Parameter Store; do not put the value in JSON, the image, source control, commands, or documentation. Configure a Linux-accessible database/authentication method and native driver; Windows LocalDB and Windows trusted authentication are not portable defaults.

Headless jobs return failure if explicitly enabled DB loading is unreachable or fails. GUI background DB processing retains its existing warning-based behavior. Database integration and authentication require a separate live test.

## Commands and exit behavior

```sh
python -B -m pricing_engine --help
python -B -m pricing_engine --check-config
python -B -m pricing_engine --ctr-file /data/ctrs/example.ctr --output-dir /data/output --workers 1 --report xlsx
python -B -m pricing_engine --ctr-file s3://input-bucket/ctrs/example.ctr --output-dir s3://output-bucket/results --report parquet
```

`--ctr-file` can be repeated, and is mutually exclusive with `--ctr-dir`. Supported report modes are `xlsx`, `html`, and `parquet`; XLSX/HTML modes also publish the companion calculation Parquet.

Logs go to stdout/stderr for ECS log-driver collection. The CLI waits for report publication and enabled DB work. Exit 0 means all selected jobs completed; pricing/report/upload/DB/configuration failures return 1; argument parsing errors return 2. `--check-config` checks syntax/settings and writable local working directories only: it does **not** check input content, installed pricing dependencies, S3 permissions, or database reachability.

## Validation

Dependency-free tests use fake S3 and stub pricing/report modules, not AWS or the real pricing math:

```sh
python -B -m unittest discover -s tests -v
```

The real smoke test is opt-in and must use the existing interpreter with the engine dependencies. It runs the repository's fictitious CTR in a subprocess, disables DB loading, writes outputs to temporary storage, and checks that XLSX/Parquet reports can be read. In PowerShell:

```powershell
$env:GAP_RUN_PRICING_SMOKE='1'; & '<existing-environment>\python.exe' -B -m unittest tests.test_pricing_smoke -v
```

Optional `GAP_SMOKE_CTR`, `GAP_SMOKE_BASIS_DIR`, and `GAP_SMOKE_ASSUMPTION_DIR` select another representative case and exact snapshot directories. An explicitly enabled smoke test fails rather than silently skipping missing dependencies. This is an execution/report smoke test, not numerical golden-baseline equivalence.

Before production, run a representative job inside the actual Linux image and a live ECS task using its task IAM role. Verify S3/mount permissions, persistent outputs, partial-upload failure handling, database mode if used, stdout logging, non-root/read-only execution, cancellation behavior, and exit codes. Windows compilation and fake-client tests alone do not certify Linux/AWS deployment.
