from __future__ import annotations

import html as _html

from .grid_extract import GridCell, GridSheet, sheet_to_grid


def grid_to_html_table(grid: GridSheet) -> str:
    rows_html = []
    for row in grid.rows:
        cells_html = []
        for cell in row:
            styles = []
            if cell.fill:
                styles.append(f"background-color:{cell.fill}")
            if cell.font_color and cell.font_color != "000000":
                styles.append(f"color:#{cell.font_color}")
            if cell.bold:
                styles.append("font-weight:bold")
            styles.append(f"text-align:{cell.align}")
            if cell.bordered:
                styles.append("border:1px solid #000")
            style_attr = ";".join(styles)
            text = _html.escape(cell.display)
            cells_html.append(f'<td style="{style_attr};padding:2px 6px;font-size:12px;">{text}</td>')
        rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    return (
        f'<table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;">'
        f'{"".join(rows_html)}</table>'
    )


def grid_to_html_with_product_pills(grid: GridSheet, section_fill: str = "#A6A6A6") -> str:
    groups = []
    current_label = None
    current_rows = []
    for row in grid.rows:
        first_cell = row[0]
        is_section_start = first_cell.bold and first_cell.fill == section_fill and first_cell.display.strip()
        if is_section_start:
            if current_label is not None:
                groups.append((current_label, current_rows))
            current_label = first_cell.display.strip()
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_label is not None:
        groups.append((current_label, current_rows))

    def _rows_to_table(rows):
        return grid_to_html_table(GridSheet(title="", n_rows=len(rows), n_cols=grid.n_cols, rows=rows))

    pill_buttons = "".join(
        f'<button class="pillbtn" data-pill="cbprod-{i}" onclick="showPill(\'{i}\')">{_html.escape(label)}</button>'
        for i, (label, _) in enumerate(groups)
    )
    panels = "".join(
        f'<div id="cbprod-{i}" class="pillpanel" style="display:{"block" if i == 0 else "none"};">{_rows_to_table(rows)}</div>'
        for i, (label, rows) in enumerate(groups)
    )

    return f"""
<style>
  .pillbar {{ margin-bottom: 10px; }}
  .pillbtn {{ border:1px solid #D7E4DF; border-radius:14px; padding:5px 16px;
              margin:2px; background:#fff; cursor:pointer; font-size:13px; }}
  .pillbtn.activepill {{ background:#009677; color:#fff; border-color:#009677; }}
</style>
<script>
  function showPill(i) {{
    document.querySelectorAll('.pillpanel').forEach(function(p) {{ p.style.display = 'none'; }});
    document.querySelectorAll('.pillbtn').forEach(function(b) {{ b.classList.remove('activepill'); }});
    document.getElementById('cbprod-' + i).style.display = 'block';
    document.querySelector('.pillbtn[data-pill="cbprod-' + i + '"]').classList.add('activepill');
  }}
  window.addEventListener('load', function() {{
    var first = document.querySelector('.pillbtn');
    if (first) first.classList.add('activepill');
  }});
</script>
<div class="pillbar">{pill_buttons}</div>
{panels}
"""


def build_full_report_html(cb: dict, po: dict, scheme_name: str = "") -> str:
    import openpyxl

    from . import category_breakdown as cbmod
    from . import pricing_outputs as pomod

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    cbmod.write_category_breakdown(wb, cb)
    pomod.write_pricing_outputs(wb, po)

    cb_grid = sheet_to_grid(wb["Category Breakdown"], title="Category Breakdown")
    po_grid = sheet_to_grid(wb["Pricing Outputs"], title="Pricing Outputs")

    cb_html = grid_to_html_with_product_pills(cb_grid)
    po_html = grid_to_html_table(po_grid)

    title = _html.escape(scheme_name or "GAP Pricing Report")
    nav_buttons = (
        '<button class="tabbtn" data-tab="cb" onclick="showTab(\'cb\')">Category Breakdown</button>'
        '<button class="tabbtn" data-tab="po" onclick="showTab(\'po\')">Pricing Outputs</button>'
    )

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 16px; }}
  .tabbar {{ margin-bottom: 12px; border-bottom: 2px solid #92D050; }}
  .tabbtn {{ background:#F4F8F6; border:1px solid #D7E4DF; border-bottom:none;
              padding:8px 20px; margin-right:4px; cursor:pointer; font-size:14px;
              border-radius:6px 6px 0 0; }}
  .tabbtn.active {{ background:#92D050; color:#fff; font-weight:bold; }}
  .tabpanel {{ overflow-x:auto; }}
</style>
<script>
  function showTab(id) {{
    document.querySelectorAll('.tabpanel').forEach(function(p) {{ p.style.display = 'none'; }});
    document.querySelectorAll('.tabbtn').forEach(function(b) {{ b.classList.remove('active'); }});
    document.getElementById('panel-' + id).style.display = 'block';
    document.querySelector('.tabbtn[data-tab="' + id + '"]').classList.add('active');
  }}
  window.onload = function() {{ document.querySelector('.tabbtn').classList.add('active'); }};
</script>
</head><body>
<h1>{title}</h1>
<div class="tabbar">{nav_buttons}</div>
<div id="panel-cb" class="tabpanel" style="display:block;">{cb_html}</div>
<div id="panel-po" class="tabpanel" style="display:none;">{po_html}</div>
</body></html>"""
