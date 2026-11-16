# Pricing Engine Deployment Audit and Dockerization Plan

## Audit summary

This audit covers the active, non-`Archive` Python engine files and directly related launcher/path-resolution code. It is a static review; it does not run the engine or validate deployment behavior. Some helper modules were checked only for search results rather than read in full. Findings below reflect the import and CLI refactor completed after the initial audit.

| Requirement | Status | Findings |
|---|---|---|
| Reads configuration from environment variables | **Partial** | `GAP_ENGINE_ROOT` selects the snapshot root in `ctr_loader/engine_paths.py:14-18`; `GAP_CTR_CACHE_DIR` and `GAP_LOCAL_S3_ROOT` override temporary working locations. Database settings such as `DB_ENABLED`, `DB_CONN_STR`, and `BATCH_DB_MAX_WORKERS` remain constants in `pricing_engine/concurrent_runner.py:23-29`. |
| Does not rely on local state/files | **Not met** | The headless runner still writes reports to the configured output directory and uses temporary staging/local storage (`pricing_engine/concurrent_runner.py:25-28,269-270`; `pricing_engine/db_loader.py:34-45`). The CTR cache now defaults to the platform temporary directory (`ctr_loader/ctr_cache.py:5`), but it is still local state. The GUI launcher still writes a log beside itself (`gap_pricing_gui/launch_gap_gui.py:16,19-27`). |
| Uses relative paths | **Partial** | The engine root defaults to the installed/source package location in `ctr_loader/engine_paths.py:14-23`; cache and local staging paths use the platform temp directory or explicit environment overrides. Inputs, snapshots, and output are supplied as CLI paths. The GUI launcher still has an absolute UNC fallback (`gap_pricing_gui/launch_gap_gui.py:11-14`). |
| Logs to stdout/stderr | **Partial / host-dependent** | The headless CLI configures logging to stdout (`pricing_engine/cli.py:38-43`). Engine modules use module loggers and rely on the host to configure handlers. The GUI launcher additionally writes a file (`gap_pricing_gui/launch_gap_gui.py:16,19-27`). |
| Can start from a single command | **Met for the headless batch runner** | Run `python -B -m pricing_engine` with CLI paths or configured environment/settings. `BackendEngine/Dockerfile` has `headless` and optional `headless-s3` targets with this exec-form entrypoint. |
| Docker targets and build context | **Implemented; image validation pending** | `BackendEngine/Dockerfile` and `BackendEngine/.dockerignore` use `BackendEngine` as the build context. The `headless` target installs `requirements-engine.txt`; `headless-s3` adds `requirements-s3.txt`; GUI `runtime` and `test` targets remain separate. Contract tests and an opt-in Docker build/runtime test are present, but a real Docker build and Linux pricing run have not been verified here. No Compose or AWS infrastructure-as-code deployment was identified. |

### Import and startup refactor

- The live `pricing_engine` and `ctr_loader` packages now use package-relative or package-qualified imports. Their runtime modules no longer edit `sys.path` or load code from fixed `J:`/UNC source roots.
- The source tree can be run without installing the project package: run `python -m pricing_engine` from the repository root. Docker should use the same model with the repository copied under its working directory; this does not require adding setuptools or another runtime library.
- GUI callers shared with the engine were switched to package imports. The separate GUI launcher and bundle/zip loader still have GUI-specific `sys.path` behavior and are not part of the headless container entrypoint.

### Notable containerization constraints

- Production target is confirmed as Linux containers on AWS. `pricing_engine/concurrent_runner.py:24` configures SQL Server LocalDB with Windows trusted authentication, which is incompatible with that target; select an AWS-accessible database and authentication method before enabling DB loading.
- Engine snapshots, CTR inputs, and generated outputs need an explicit container data contract: either mounted input/output directories or an approved external storage service. They must not implicitly depend on a developer's mapped drive or home directory.
- The GUI and headless pricing engine are different workloads. This plan targets the headless engine; GUI-in-container support is out of scope unless separately requested.

## Dockerization plan of action

1. **Define the container workload and AWS compute model.** Keep Linux as the production image target; select where the FastAPI service runs and how batch pricing jobs are scheduled (for example, ECS Fargate and/or AWS Batch). Confirm the supported Python version and whether database loading is required. Keep the GUI out of the initial image unless interactive GUI deployment is an explicit requirement.
2. **Inventory runtime dependencies and data inputs.** Trace imports and runtime-loaded modules, identify pinned dependency versions/native drivers (including Polars and `pyodbc`), and list required CTR files, basis/assumption snapshots, and report/output locations. Confirm any licensing or access requirements for those files.
3. **Resolve configuration and data boundaries.** Replace hard-coded roots, database settings, and worker configuration with documented environment-driven settings. Resolve application paths relative to the package/image and require external data roots to be explicitly configured or mounted. Keep secrets out of the image and source control.
4. **Use the deterministic headless entrypoint.** The headless image runs `python -B -m pricing_engine`, with CLI arguments, configuration validation, and meaningful exit codes. Keep batch execution independent of the GUI launcher and Visual Studio's working directory.
5. **Validate and release the container image.** Build `BackendEngine/Dockerfile` with the `BackendEngine` context. Keep the image non-root and read-only compatible, use explicit mounts or approved external storage for inputs and persistent outputs, and include the platform-appropriate ODBC driver only if database loading is approved. Pin or control release base images and dependency sources.
6. **Make container behavior observable and configurable.** Configure Python logging to stdout/stderr, remove the dependency on adjacent log files, document required/optional environment variables, and ensure termination produces a meaningful exit status.
7. **Add local run configuration and usage documentation.** Add a Compose file only if it improves local setup (for example, mounts and environment wiring); do not bake credentials into it. Document build, run, volume, and configuration commands.
8. **Validate the image and runtime contract.** Run `tests.test_docker_contract` and the opt-in `tests.test_docker_runtime` test. Then build on a clean checkout and run a representative headless pricing job with approved fixtures; verify logs, mounted inputs, persistent outputs, and database-enabled/disabled behavior. Record the tested platform and dependency versions. The opt-in config check is not a substitute for a real pricing run.

### Decisions required before implementation

- Which AWS compute model should host the API and batch jobs (for example, ECS Fargate, AWS Batch, or another approved service)?
- Are basis/assumption snapshots and CTR inputs supplied by mounted directories, an external storage service, or another mechanism?
- Is database loading mandatory, optional, or disabled in the initial container? If mandatory, what SQL Server endpoint and authentication method should replace LocalDB/trusted authentication?
- Where will the image run (developer workstation, CI, or a deployment platform), and what resource/concurrency limits apply?
