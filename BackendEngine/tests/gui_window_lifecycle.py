"""Real Qt window lifecycle checks, run by the container test target."""
from __future__ import annotations

import os
import signal
import threading
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets

from gap_config import ConfigurationError
from gap_pricing_gui.main_window import GapWindow
from gap_pricing_gui.launch_gap_gui import _install_sigterm_handler


class WaitingThread(QtCore.QThread):
	def __init__(self, release):
		super().__init__()
		self.release = release

	def run(self):
		self.release.wait(5)


class WindowLifecycleTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
		cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

	def test_invalid_configuration_shows_error_window_without_starting_workers(self):
		with patch("gap_pricing_gui.main_window.validate_gui_settings",
				   side_effect=ConfigurationError("missing input mount")):
			window = GapWindow()
		try:
			self.assertIsNone(window.settings)
			self.assertIn("missing input mount", window.centralWidget().text())
			self.assertIsNone(getattr(window, "_startup", None))
			window.show()
			self.app.processEvents()
			self.assertTrue(window.isVisible())
		finally:
			window.close()
			self.app.processEvents()

	def test_sigterm_queues_window_close_and_restores_previous_handler(self):
		class FakeSignals:
			SIGTERM = signal.SIGTERM

			def __init__(self):
				self.handler = object()

			def getsignal(self, signum):
				return self.handler

			def signal(self, signum, handler):
				previous = self.handler
				self.handler = handler
				return previous

		queued = []
		class FakeQtCore:
			class QTimer:
				@staticmethod
				def singleShot(delay, callback):
					queued.append((delay, callback))

		class FakeWindow:
			closed = False

			def close(self):
				self.closed = True

		signals = FakeSignals()
		original = signals.handler
		window = FakeWindow()
		restore = _install_sigterm_handler(window, FakeQtCore, signals)
		installed = signals.handler
		installed(signal.SIGTERM, None)
		self.assertEqual(len(queued), 1)
		self.assertEqual(queued[0][0], 0)
		self.assertFalse(window.closed)
		queued[0][1]()
		self.assertTrue(window.closed)
		restore()
		self.assertIs(signals.handler, original)

	def test_close_waits_for_startup_batch_and_all_view_threads(self):
		with patch("gap_pricing_gui.main_window.validate_gui_settings",
				   side_effect=ConfigurationError("missing input mount")):
			window = GapWindow()
		release = threading.Event()
		threads = [WaitingThread(release) for _ in range(4)]
		window._startup, window._batch_thread = threads[:2]
		window._view_prewarm_threads = threads[2:]
		try:
			window.show()
			for thread in threads:
				thread.start()
			deadline = time.monotonic() + 5
			while not all(thread.isRunning() for thread in threads) and time.monotonic() < deadline:
				self.app.processEvents()
				time.sleep(0.01)
			self.assertTrue(all(thread.isRunning() for thread in threads))
			window.close()
			self.assertTrue(window.isVisible(), "Closing must be deferred while workers are running")
			release.set()
			for thread in threads:
				self.assertTrue(thread.wait(5000))
			deadline = time.monotonic() + 5
			while window.isVisible() and time.monotonic() < deadline:
				self.app.processEvents()
				time.sleep(0.02)
			self.assertFalse(window.isVisible(), "Window should close after workers finish")
		finally:
			release.set()
			for thread in threads:
				thread.wait(5000)
			window.close()
			self.app.processEvents()


if __name__ == "__main__":
	unittest.main()
