# Configuration/storage validation record

## Completed in the current workspace

Interpreter: discovered system Python 3.10.5 on Windows. No dependencies were installed or upgraded.

| Check | Result |
|---|---|
| `python -B -m unittest discover -s tests -v` | 49 tests: 48 passed, 1 opt-in real-pricing smoke test skipped |
| Python compilation | 66 runtime/test files compiled successfully, with bytecode directed to a temporary cache |
| Runtime import/path AST scan | 59 runtime files; no detected bare project `from` imports or `sys.path` calls |
| `python -B -m pricing_engine --check-config` | Passed |
| Visual Studio file diagnostics | No diagnostics returned for the inspected configuration, storage, runner and test files |
| `git diff --check` | Passed; Git reported only LF-to-CRLF conversion notices for the existing Windows launchers |
| Visual Studio solution build | Unavailable: `Build.BuildSolution` is not exposed for this workspace |

Tests cover JSON/environment/CLI precedence, secret exclusion, malformed S3 locations, filesystem/S3 staging and publication, paginated prefix listing, traversal rejection, task-role-compatible lazy client creation, atomic download failure behavior, snapshot selection, optional DB mode, report completion, missing outputs, upload/report/DB failures, and staging cleanup.

The S3 client, pricing engine, and report writer are test doubles in the dependency-free integration tests. These results do not establish live AWS access or pricing-calculation correctness. Existing tracked bytecode files modified by the earlier compilation pass were restored; subsequent tests use `-B` and compilation uses a temporary cache.

## Pending: representative pricing job

The user selected an **existing Python environment**, but its `python.exe` path has not yet been supplied. The discovered interpreter lacks core pricing/report libraries and boto3. No alternative environment was identified through the Python launcher or active environment variables. A real pricing job has not been reported as passed.

The executable smoke test is `tests/test_pricing_smoke.py`. Enable it with `GAP_RUN_PRICING_SMOKE=1` and run it using that existing interpreter. It uses the repository fictitious single-run CTR, disables DB loading, runs the headless CLI, and verifies readable XLSX and nonempty calculation Parquet output. When explicitly enabled, missing dependencies cause a failure rather than a skip.

See [ECS_LINUX.md](ECS_LINUX.md) for invocation and fixture/snapshot override variables.

## Deployment checks not performed

- Actual Linux container build/execution and non-root/read-only filesystem behavior.
- Live S3 access through an ECS task IAM role and production bucket/KMS policies.
- Live database loading/native ODBC driver/authentication.
- Numerical comparison against an approved golden pricing baseline.

These remain separate deployment acceptance checks; passing Windows compilation and fake-client regressions does not replace them.
