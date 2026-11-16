from __future__ import annotations

import re
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND_ROOT / "Dockerfile"
DOCKERIGNORE = BACKEND_ROOT / ".dockerignore"


def docker_stage(name: str) -> str:
	text = DOCKERFILE.read_text(encoding="utf-8")
	stages = list(re.finditer(r"(?im)^FROM\s+\S+\s+AS\s+(?P<name>[^\s]+)\s*$", text))
	for index, match in enumerate(stages):
		if match.group("name").lower() == name.lower():
			end = stages[index + 1].start() if index + 1 < len(stages) else len(text)
			return text[match.start():end]
	raise AssertionError(f"Docker stage {name!r} is missing")


class DockerContractTests(unittest.TestCase):
	def test_headless_target_uses_engine_dependencies_and_entrypoint(self):
		base = docker_stage("engine-base")
		headless = docker_stage("headless")
		self.assertIn("COPY requirements-engine.txt /app/requirements-engine.txt", base)
		self.assertIn("RUN python -m pip install --no-cache-dir -r /app/requirements-engine.txt", base)
		self.assertIn("COPY pricing_engine/ /app/pricing_engine/", base)
		self.assertIn("COPY ctr_loader/ /app/ctr_loader/", base)
		self.assertIn("COPY reporting/ /app/reporting/", base)
		self.assertIn("COPY config.json /app/config.json", base)
		self.assertIn("RUN python -B -m pricing_engine --check-config", base)
		self.assertNotIn("apt-get", base)
		self.assertNotIn("gap_pricing_gui", base)
		self.assertIn('ENTRYPOINT ["python", "-B", "-m", "pricing_engine"]', headless)

	def test_headless_image_defaults_to_non_root_and_database_disabled(self):
		base = docker_stage("engine-base")
		self.assertIn("USER 10001:10001", base)
		self.assertIn("GAP_DB_ENABLED=false", base)
		self.assertIn("/data/output", base)
		self.assertIn("/tmp/gap", base)

	def test_optional_s3_target_installs_existing_s3_requirements(self):
		s3 = docker_stage("headless-s3")
		self.assertIn("COPY requirements-s3.txt /app/requirements-s3.txt", s3)
		self.assertIn("RUN python -m pip install --no-cache-dir -r /app/requirements-s3.txt", s3)
		self.assertIn('ENTRYPOINT ["python", "-B", "-m", "pricing_engine"]', s3)

	def test_gui_runtime_and_container_test_targets_are_preserved(self):
		app = docker_stage("app")
		test = docker_stage("test")
		runtime = docker_stage("runtime")
		self.assertIn("COPY gap_pricing_gui/requirements.txt", app)
		self.assertIn('ENTRYPOINT ["python", "-B", "tests/run_container_tests.py"]', test)
		self.assertIn('ENTRYPOINT ["python", "-B", "-m", "gap_pricing_gui.launch_gap_gui"]', runtime)

	def test_dockerignore_allows_required_headless_manifests(self):
		ignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
		self.assertIn("!requirements-engine.txt", ignore)
		self.assertIn("!requirements-s3.txt", ignore)


if __name__ == "__main__":
	unittest.main()
