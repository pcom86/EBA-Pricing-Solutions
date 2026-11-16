FROM python:3.12-slim-bookworm AS engine-base

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	GAP_CONFIG_FILE=/app/config.json \
	GAP_INPUT_DIR=/data/input \
	GAP_OUTPUT_DIR=/data/output \
	GAP_BASIS_ROOT=/data/basis \
	GAP_ASSUMPTION_ROOT=/data/assumptions \
	GAP_CTR_CACHE_DIR=/tmp/gap/cache \
	GAP_STAGING_DIR=/tmp/gap/staging \
	GAP_ARTIFACT_ROOT=/data/output/artifacts \
	GAP_DB_ENABLED=false \
	HOME=/tmp/gap

WORKDIR /app
COPY requirements-engine.txt /app/requirements-engine.txt
RUN python -m pip install --no-cache-dir -r /app/requirements-engine.txt

COPY gap_config/ /app/gap_config/
COPY pricing_engine/ /app/pricing_engine/
COPY ctr_loader/ /app/ctr_loader/
COPY reporting/ /app/reporting/
COPY config.json /app/config.json

RUN groupadd --gid 10001 gap && useradd --uid 10001 --gid gap --no-create-home gap \
	&& mkdir -p /data/input /data/output /data/basis /data/assumptions /tmp/gap \
	&& chown gap:gap /data/output /tmp/gap
USER 10001:10001
RUN python -B -m pricing_engine --check-config

FROM engine-base AS headless
ENTRYPOINT ["python", "-B", "-m", "pricing_engine"]

FROM engine-base AS headless-s3
USER root
COPY requirements-s3.txt /app/requirements-s3.txt
RUN python -m pip install --no-cache-dir -r /app/requirements-s3.txt
USER 10001:10001
ENTRYPOINT ["python", "-B", "-m", "pricing_engine"]

FROM engine-base AS app
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
	fonts-dejavu-core libdbus-1-3 libegl1 libfontconfig1 libgl1 libglib2.0-0 \
	libice6 libodbc2 libopengl0 libsm6 libx11-xcb1 libxi6 libxrender1 \
	libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
	libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
	libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 \
	&& rm -rf /var/lib/apt/lists/*
COPY gap_pricing_gui/requirements.txt /app/gap_pricing_gui/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/gap_pricing_gui/requirements.txt
COPY gap_pricing_gui/ /app/gap_pricing_gui/
USER 10001:10001
RUN QT_QPA_PLATFORM=offscreen python -B -c "from PySide6.QtWidgets import QApplication, QLabel; import polars, pandas, openpyxl, python_calamine, rapidfuzz, xlsxwriter, pyodbc; app = QApplication([]); label = QLabel('Qt image check'); label.show(); app.processEvents()"

FROM app AS test
COPY tests/ /app/tests/
ENV QT_QPA_PLATFORM=offscreen
ENTRYPOINT ["python", "-B", "tests/run_container_tests.py"]

FROM app AS runtime
ENTRYPOINT ["python", "-B", "-m", "gap_pricing_gui.launch_gap_gui"]
