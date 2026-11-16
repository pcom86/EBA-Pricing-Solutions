from __future__ import annotations

from PySide6 import QtCore

from .config import snapshot_folders


class StartupThread(QtCore.QThread):
    done = QtCore.Signal(object, object)
    failed = QtCore.Signal(str)

    def run(self) -> None:
        try:
            from pricing_engine.concurrent_runner import load_shared_snapshots
            basis_folder, assump_folder = snapshot_folders()
            basis, assump = load_shared_snapshots(basis_folder, assump_folder)
            self.done.emit(basis, assump)
        except Exception:
            import traceback
            self.failed.emit(traceback.format_exc())


class BatchThread(QtCore.QThread):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, paths, basis, assump, out_dir, max_workers,
                 write_report: bool | str = False):
        super().__init__()
        self.paths = paths
        self.basis = basis
        self.assump = assump
        self.out_dir = out_dir
        self.max_workers = max_workers
        self.write_report = write_report
        self.results_cache: dict[str, tuple] = {}

    def _sink(self, ctr_path, ctr, assump, all_results) -> None:
        self.results_cache[str(ctr_path)] = (ctr, assump, all_results)

    def run(self) -> None:
        self.results_cache = {}
        try:
            from pricing_engine.concurrent_runner import run_quotes_concurrent
            df = run_quotes_concurrent(
                self.paths, self.basis, self.assump, self.out_dir,
                max_workers=self.max_workers, verbose=False,
                write_report=self.write_report,
                result_sink=self._sink,
                wait_for_reports=True,
            )
            self.done.emit(df)
        except Exception:
            import traceback
            self.failed.emit(traceback.format_exc())


class ViewPrewarmThread(QtCore.QThread):
    done = QtCore.Signal(object, object, object)
    failed = QtCore.Signal(str)

    def __init__(self, ctr, assump, per_product_results):
        super().__init__()
        self.ctr = ctr
        self.assump = assump
        self.per_product_results = per_product_results

    def run(self) -> None:
        try:
            import openpyxl
            from reporting.inputs import gather_cb_data, gather_po_data
            from reporting.category_breakdown import write_category_breakdown
            from reporting.pricing_outputs import write_pricing_outputs
            from reporting.grid_extract import sheet_to_grid

            cb_data = gather_cb_data(self.per_product_results, self.ctr.scheme,
                                      self.assump, ctr=self.ctr)
            wb_cb = openpyxl.Workbook(); wb_cb.remove(wb_cb.active)
            write_category_breakdown(wb_cb, cb_data)
            cb_grid = sheet_to_grid(wb_cb["Category Breakdown"], title="Category Breakdown")

            po_data = gather_po_data(self.per_product_results, self.ctr.scheme,
                                      assump=self.assump, ctr=self.ctr)
            wb_po = openpyxl.Workbook(); wb_po.remove(wb_po.active)
            write_pricing_outputs(wb_po, po_data)
            po_grid = sheet_to_grid(wb_po["Pricing Outputs"], title="Pricing Outputs")

            from .po_view import filter_po_grid_to_active
            po_grid = filter_po_grid_to_active(
                po_grid, po_data["products"], getattr(self.ctr, "active_products", None)
            )

            self.done.emit(cb_grid, po_grid, po_data["products"])
        except Exception:
            import traceback
            self.failed.emit(traceback.format_exc())
