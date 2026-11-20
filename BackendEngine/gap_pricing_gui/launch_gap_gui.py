
import logging
import signal
import sys
import time
import traceback
from datetime import datetime

from gap_pricing_gui.file_access import validate_gui_settings

logger = logging.getLogger(__name__)


def _install_sigterm_handler(window, qt_core, signal_module=signal):
    previous_handler = signal_module.getsignal(signal_module.SIGTERM)

    def _handle_sigterm(signum, frame):
        logger.info("Received SIGTERM; requesting orderly GUI shutdown.")
        qt_core.QTimer.singleShot(0, window.close)

    signal_module.signal(signal_module.SIGTERM, _handle_sigterm)
    return lambda: signal_module.signal(signal_module.SIGTERM, previous_handler)


def _log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    logger.info(line)


def main() -> int:
    import time as _time
    globals()["_BOOT_T0"] = _time.perf_counter()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    settings = validate_gui_settings()
    _log(f"Configuration: {settings.config_file}")

    from PySide6 import QtCore, QtWidgets

    if hasattr(QtCore.Qt, "HighDpiScaleFactorRoundingPolicy"):
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    import threading

    from gap_pricing_gui import warm_state

    def _warm_ui():
        try:
            import polars  # noqa: F401 - pre-import to warm module cache
            import reporting.category_breakdown
            import reporting.inputs
            import reporting.pricing_outputs
            import reporting.writer  # noqa: F401 - pre-import to warm module cache
            _log("background warm: polars + reporting submodules ready.")
        except Exception as e:  # noqa: BLE001 - warm-up must never crash the launcher
            _log(f"background warm skipped (reporting/polars): {type(e).__name__}: {e}")
        finally:
            warm_state.UI_WARM_DONE.set()

    def _warm_engine():
        try:
            from gap_pricing_gui import quote_runner  # noqa: F401 - pre-import to warm module cache
            _log("background warm: quote_runner ready.")
        except Exception as e:  # noqa: BLE001 - warm-up must never crash the launcher
            _log(f"background warm skipped (quote_runner): {type(e).__name__}: {e}")
        finally:
            warm_state.ENGINE_WARM_DONE.set()

    threading.Thread(target=_warm_ui, name="gap-warm-ui", daemon=True).start()
    threading.Thread(target=_warm_engine, name="gap-warm-engine", daemon=True).start()

    def _sweep_stale():
        try:
            import os  # noqa: F401 - pre-import to warm module cache
            import tempfile  # noqa: F401 - pre-import to warm module cache

            from gap_pricing_gui import warm_state
            staging_root = str(settings.staging_dir)
            removed = warm_state.sweep_stale_staging(staging_root, max_age_hours=24.0)
            if removed:
                _log(f"cache sweep: removed {removed} stale staging folder(s).")
        except Exception as e:  # noqa: BLE001 - cache sweep must never crash the launcher
            _log(f"cache sweep skipped: {type(e).__name__}: {e}")

    threading.Thread(target=_sweep_stale, name="gap-cache-sweep", daemon=True).start()

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("GAP Pricing Engine")

    from gap_pricing_gui.theme import apply_light_theme
    apply_light_theme(app)

    from gap_pricing_gui.main_window import GapWindow

    _boot_t0 = globals().get("_BOOT_T0")
    win = GapWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    if _boot_t0 is not None:
        _log(f"GUI_READY_COLD_SECONDS={time.perf_counter() - _boot_t0:.2f}")
    _log("gap_pricing_gui window launched.")

    app._gap_main_window = win

    if owns_app:
        restore_sigterm = _install_sigterm_handler(win, QtCore)
        try:
            return app.exec()
        finally:
            restore_sigterm()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - top-level fatal handler must catch everything
        _log("FATAL during launch:\n" + traceback.format_exc())
        sys.exit(1)
