from __future__ import annotations

__all__ = [
    "gather_cb_data", "gather_po_data",
    "write_category_breakdown", "write_pricing_outputs",
    "write_pricing_report",
]

_LAZY = {
    "gather_cb_data": (".inputs", "gather_cb_data"),
    "gather_po_data": (".inputs", "gather_po_data"),
    "write_category_breakdown": (".category_breakdown", "write_category_breakdown"),
    "write_pricing_outputs": (".pricing_outputs", "write_pricing_outputs"),
    "write_pricing_report": (".writer", "write_pricing_report"),
}


def __getattr__(name):
    if name in _LAZY:
        mod_name, attr = _LAZY[name]
        import importlib
        mod = importlib.import_module(mod_name, __name__)
        val = getattr(mod, attr)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
