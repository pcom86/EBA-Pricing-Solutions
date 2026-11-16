# Combined GUI and engine container

## Scope

The initial image contains `gap_pricing_gui`, `gap_config`, `pricing_engine`,
`ctr_loader`, and `reporting` together. It starts the existing PySide6 application,
not a web application. No API, remote desktop server, VNC/noVNC stack, AWS
infrastructure, or database schema is provisioned. See `ECS_LINUX.md` for the
separate headless execution contract.

The GUI accepts filesystem locations visible **inside the container**. A file
picker cannot browse a remote user's workstation. Mount approved input files
first. S3 locations are rejected by GUI path validation; the headless engine's
existing S3 adapter is unchanged.

## Build

From the repository root, using a Linux Docker engine:

```sh
docker build --target runtime -t gap-gui:local .
docker build --target test -t gap-gui:test .
```

The image uses Python 3.12 on Debian Bookworm, the existing pinned GUI Python
requirements, and native Qt/X11 libraries. It does not install the project as a
package or modify `sys.path`. The build checks real Qt offscreen initialization.
No packages need to be installed on the host.

The build-context allowlist excludes CTRs, snapshots, generated reports, Git
history, notebooks, credentials, and bytecode. Only Python source, the existing
GUI dependency manifest, and `config.json` are copied. Runtime excludes tests.
Review `config.json` before building: it must never contain secrets.

For release builds, record the built image digest and vulnerability scan results.
The base tag and OS package repository are not immutable; the target makes test
execution repeatable, not image builds byte-for-byte reproducible. Pin an approved
base-image digest in your release pipeline when available.

## Configuration and mounts

All application settings still come from `gap_config`: environment overrides
JSON, and relative filesystem settings resolve against the configuration file's
parent directory. Image defaults override the repository's developer paths:

| Container location | Purpose | Access |
| --- | --- | --- |
| `/data/input` | CTR directory (`GAP_INPUT_DIR`) | read-only |
| `/data/basis` | basis snapshot root (`GAP_BASIS_ROOT`) | read-only |
| `/data/assumptions` | assumption root (`GAP_ASSUMPTION_ROOT`) | read-only |
| `/data/output` | reports (`GAP_OUTPUT_DIR`) | writable |
| `/tmp/gap/cache` | CTR cache (`GAP_CTR_CACHE_DIR`) | writable, ephemeral |
| `/tmp/gap/staging` | report staging (`GAP_STAGING_DIR`) | writable, ephemeral |

Mount snapshot **roots**, containing the selected snapshot directories. Set
`GAP_BASIS_SNAPSHOT` and `GAP_ASSUMPTION_SNAPSHOT` to the actual folder names;
repository defaults are `basis_v2026_08` and `assumption_A2026_08`.
A custom JSON file can be mounted read-only and selected with `GAP_CONFIG_FILE`.
Override the image's path environment variables too if changing their locations.

The runtime runs as UID/GID `10001:10001`. Precreate the host output directory and
grant that UID write access. Grant it read/traverse access to input/snapshot
mounts. Do not solve mount permissions by running the application as root.
The application validates readable directories and probes output/cache/staging
writability. GUI single saves report publication errors to the user, and batch
completion now waits for reports in its background worker. This does not make
multi-file exports transactional, nor redesign shutdown during active pricing.

On Linux, CTRs from filesystems identified as network-backed by
`/proc/self/mountinfo` (including NFS, CIFS/SMB, SSHFS, and Ceph mounts) are
copied through `GAP_CTR_CACHE_DIR` before loading. Ordinary local filesystem
paths are read directly. Windows UNC caching remains enabled. If mount metadata
is unavailable, Linux inputs are read directly rather than copied based on a
path-name guess.

On SIGTERM, the launcher requests normal Qt window closure. The window defers
closure while startup, batch, or view threads run and while report/database
writes are pending. Set the orchestrator stop grace period long enough for the
longest expected pricing task plus the existing 15-second background-write
grace. If the grace period expires, Docker may force-kill the process and active
work can be interrupted.

`GAP_PRICING_WORKERS`, `GAP_REPORT_WORKERS`, and `GAP_DB_WORKERS` retain their
existing meanings. DB defaults disabled and its GUI button is disabled. The
image includes the existing `pyodbc` dependency and unixODBC runtime but **not**
a SQL Server ODBC driver. Enabling DB requires a separately approved driver,
connectivity, schema, injected connection string, and additional integration tests.
Do not bake credentials into the image. S3 SDKs are not required for this GUI.

## Interactive display

A supplied display is required. `QT_QPA_PLATFORM=offscreen` is for tests only; it
does not provide an interactive desktop or a browser endpoint. On Linux with an
approved X11 display and an Xauthority file readable by UID 10001, an example is:

```sh
docker run --rm --init --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  -e QT_QPA_PLATFORM=xcb -e DISPLAY -e XAUTHORITY=/run/gap.xauthority \
  -e GAP_BASIS_SNAPSHOT=basis_v2026_08 -e GAP_ASSUMPTION_SNAPSHOT=assumption_A2026_08 \
  --mount type=bind,src=/tmp/.X11-unix,dst=/tmp/.X11-unix,readonly \
  --mount type=bind,src=/absolute/display.xauthority,dst=/run/gap.xauthority,readonly \
  --mount type=bind,src=/absolute/approved/ctrs,dst=/data/input,readonly \
  --mount type=bind,src=/absolute/approved/basis,dst=/data/basis,readonly \
  --mount type=bind,src=/absolute/approved/assumptions,dst=/data/assumptions,readonly \
  --mount type=bind,src=/absolute/output,dst=/data/output \
  gap-gui:local
```

Use a dedicated, trusted display session; X11 access carries security risks.
Do not disable access control globally with `xhost +`. AWS/ECS has no automatic
desktop display; remote-display access and authentication must be designed and
validated separately. Windows Docker Desktop likewise does not supply an X11
server automatically. The commands above assume a Linux host/display.

## Required CI integration run

Provide approved/sanitized fixtures outside the image. Mount at least two valid
CTRs in the input root (not nested), with compatible basis/assumption snapshots.
Set `GAP_GUI_TEST_CTR` to one mounted file with active products and positive
premium. `.ctr` and `.CTR` are recognized. Use an empty writable output directory.

```sh
docker run --rm --init --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  -e GAP_GUI_TEST_CTR=/data/input/representative.ctr \
  -e GAP_BASIS_SNAPSHOT=basis_v2026_08 -e GAP_ASSUMPTION_SNAPSHOT=assumption_A2026_08 \
  --mount type=bind,src=/absolute/approved/ctrs,dst=/data/input,readonly \
  --mount type=bind,src=/absolute/approved/basis,dst=/data/basis,readonly \
  --mount type=bind,src=/absolute/approved/assumptions,dst=/data/assumptions,readonly \
  --mount type=bind,src=/absolute/ci-output,dst=/data/output \
  gap-gui:test
```

The target forces DB off and Qt offscreen. It runs dependency-light regressions
and real GUI integration in separate processes, each with a 30-minute timeout.
It does **not** skip integration when dependencies or fixtures are absent. A
missing `GAP_GUI_TEST_CTR` exits with code 2; test failures produce a nonzero exit.
The older opt-in CLI smoke may be reported skipped by unit discovery; the real
GUI integration still runs explicitly afterward.

The real test launches the application, loads actual snapshots, checks image
import origins, exercises dialog selection/cancel (only the dialog responses are
simulated), prices a real single quote, builds Category Breakdown/Pricing Outputs,
saves readable XLSX/Parquet/HTML, tests publication failure propagation, prices
a real batch, opens batch-detail views, and exports HTML. It verifies that code
and input mounts are not writable. Temporary test reports are removed afterward.

This tests real workflow execution and artifact structure, not approved numerical
golden values, all business-rule scenarios, DB integration, or remote-display
connectivity. Add approved expected pricing baselines for release acceptance.
An interactive smoke test must still verify visibility, browsing mounted files,
pricing, reports, and clean exit on the chosen display platform.

## Local validation without Docker

```sh
python -B -m unittest discover -s tests -v
```

This runs configuration/storage/report mocks and filesystem regressions. It is
not a replacement for the required container run. The development machine used
for this change had neither Docker nor the Qt/pricing dependencies, so Linux
build and real GUI execution remain pending on the supplied Docker environment.
