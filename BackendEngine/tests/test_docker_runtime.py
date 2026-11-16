from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOCKER_ENABLED = os.environ.get("GAP_RUN_DOCKER_TESTS") == "1"


@unittest.skipUnless(DOCKER_ENABLED, "Set GAP_RUN_DOCKER_TESTS=1 to build and run the headless Docker image")
class HeadlessDockerRuntimeTests(unittest.TestCase):
	def test_headless_image_builds_and_runs_read_only_config_check(self):
		docker = shutil.which("docker")
		self.assertIsNotNone(docker, "GAP_RUN_DOCKER_TESTS=1 requires Docker on PATH")
		image = f"gap-pricing-headless-test:{uuid.uuid4().hex}"
		try:
			build = subprocess.run(
				[docker, "build", "--file", str(BACKEND_ROOT / "Dockerfile"),
				 "--target", "headless", "--tag", image, str(BACKEND_ROOT)],
				check=False, timeout=1800,
			)
			self.assertEqual(build.returncode, 0, "Headless Docker image build failed")
			run = subprocess.run(
				[docker, "run", "--rm", "--init", "--read-only", "--cap-drop", "ALL",
				 "--security-opt", "no-new-privileges",
				 "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
				 "--entrypoint", "python", image, "-B", "-m", "pricing_engine", "--check-config"],
				check=False, timeout=180,
			)
			self.assertEqual(run.returncode, 0, "Headless container config/runtime check failed")
		finally:
			subprocess.run(
				[docker, "image", "rm", "--force", image],
				check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
			)


if __name__ == "__main__":
	unittest.main()
