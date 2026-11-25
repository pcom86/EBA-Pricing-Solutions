from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl


@dataclass(frozen=True)
class SchemeRecord:
    quote_type: str | None = None
    quote_group: str | None = None
    quote_number: int | None = None
    doer: str | None = None
    checker: str | None = None
    scheme_name: str | None = None
    scheme_codes: dict[str, str | None] = field(default_factory=dict)

    effective_date: date | None = None
    calculation_date: date | None = None
    commencement_date: date | None = None

    intermediary: str | None = None
    broker_name: str | None = None
    client_manager: str | None = None
    branch: str | None = None
    alexander_forbes_scheme: str | None = None
    country_scale: str | None = None

    pct_comm_applied: float | None = None
    initial_commission: str | None = None
    collection_commission: str | None = None
    collection_fee_pct: float | None = None
    include_vat_commission: str | None = None
    namibian_stamp_duty: str | None = None
    premium_frequency: str | None = None

    fee_structure: str | None = None
    annual_fee_gla: float | None = None
    admin_fee_pct_gla: float | None = None
    annual_fee_gip: float | None = None
    admin_fee_pct_gip: float | None = None
    annual_fee_fc: float | None = None
    admin_fee_pct_fc: float | None = None
    annual_fee_tip: float | None = None
    admin_fee_pct_tip: float | None = None

    primary: str | None = None
    secondary: str | None = None
    region: str | None = None
    regions: dict[str, float] = field(default_factory=dict)

    salary_definition: str | None = None
    pear_tgp_pct: float | None = None
    profit_refund_loading_by_product: dict[str, float] = field(default_factory=dict)
    rate_basis_by_product: dict[str, float] = field(default_factory=dict)
    priced_margin_overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    rating: dict[str, str] = field(default_factory=dict)
    pricing_basis: dict[str, float] = field(default_factory=dict)
    fees_priced: dict[str, float] = field(default_factory=dict)
    basic_fees: dict[str, float] = field(default_factory=dict)

    eswatini: float | None = None

    spouse_use_sp_sheet: bool = False

    express_over_gla: dict[str, bool] = field(default_factory=dict)
    category_split: dict[str, str] = field(default_factory=dict)
    max_cover_per_category: dict[str, dict[int, float | None]] = field(default_factory=dict)
    fc_max_no_of_spouses: dict[int, float] = field(default_factory=dict)
    fc_parents_included: dict[int, str] = field(default_factory=dict)
    fc_parents_in_law_included: dict[int, str] = field(default_factory=dict)
    fc_include_ctc: dict[int, str] = field(default_factory=dict)
    fc_paid_up_benefits_active: dict[int, str] = field(default_factory=dict)
    fc_pup_death: dict[int, str] = field(default_factory=dict)
    fc_pup_lsdb: dict[int, str] = field(default_factory=dict)
    fc_pup_death_lsdb: dict[int, str] = field(default_factory=dict)

    fc_benefit_amounts: dict[int, dict[str, float]] = field(default_factory=dict)

    ext_fc_extended_family_selected: dict[int, str] = field(default_factory=dict)
    ext_fc_parents_selected: dict[int, str] = field(default_factory=dict)
    ext_fc_pct_male: dict[int, float] = field(default_factory=dict)
    ext_fc_avg_salary: dict[int, float] = field(default_factory=dict)
    ext_fc_parent_dist_available: dict[int, str] = field(default_factory=dict)
    ext_fc_family_dist_available: dict[int, str] = field(default_factory=dict)

    ext_fc_age_bands: dict[int, dict[str, list]] = field(default_factory=dict)

    gip_nra: dict[int, object] = field(default_factory=dict)
    gip_cover_type: dict[int, str] = field(default_factory=dict)
    gip_tax_options: dict[int, str] = field(default_factory=dict)
    gip_escalation_type: dict[int, str] = field(default_factory=dict)
    gip_escalation_pct: dict[int, float] = field(default_factory=dict)
    gip_waiting_period: dict[int, str] = field(default_factory=dict)
    gip_backdating: dict[int, str] = field(default_factory=dict)
    gip_include_conversion_option: dict[int, str] = field(default_factory=dict)
    gip_pre_ex_before: dict[int, str] = field(default_factory=dict)
    gip_pre_ex_after: dict[int, str] = field(default_factory=dict)
    gip_initial_period: dict[int, str] = field(default_factory=dict)
    gip_core_disability_definition: dict[int, str] = field(default_factory=dict)
    gip_core_benefit_perc: dict[int, list] = field(default_factory=dict)
    gip_core_benefit_layer: dict[int, list] = field(default_factory=dict)
    gip_core_benefit_perc_additional: dict[int, float] = field(default_factory=dict)
    gip_core_benefit_layer_additional: dict[int, float] = field(default_factory=dict)
    gip_core_overall_maximum: dict[int, float] = field(default_factory=dict)
    gip_employer_waiver: dict[int, float] = field(default_factory=dict)
    gip_employee_waiver: dict[int, float] = field(default_factory=dict)
    gip_hcc_disability_definition: dict[int, str] = field(default_factory=dict)
    gip_hcc_benefit_perc: dict[int, list] = field(default_factory=dict)
    gip_hcc_benefit_layer: dict[int, list] = field(default_factory=dict)
    gip_hcc_overall_maximum: dict[int, float] = field(default_factory=dict)
    gip_include_hcc_waiver: dict[int, str] = field(default_factory=dict)
    gip_hcc_payment_period: dict[int, float] = field(default_factory=dict)
    gip_hcc_payment_type: dict[int, str] = field(default_factory=dict)
    gip_hcc_nominal_amount: dict[int, float] = field(default_factory=dict)
    gip_hcc_escalation_type: dict[int, str] = field(default_factory=dict)
    gip_hcc_escalation_pct: dict[int, float] = field(default_factory=dict)
    gip_waiver_pct_specified: dict[int, str] = field(default_factory=dict)

    tip_nra: dict[int, object] = field(default_factory=dict)
    tip_cover_type: dict[int, str] = field(default_factory=dict)
    tip_tax_options: dict[int, str] = field(default_factory=dict)
    tip_escalation_type: dict[int, str] = field(default_factory=dict)
    tip_escalation_pct: dict[int, float] = field(default_factory=dict)
    tip_waiting_period: dict[int, str] = field(default_factory=dict)
    tip_backdating: dict[int, str] = field(default_factory=dict)
    tip_pre_ex_before: dict[int, str] = field(default_factory=dict)
    tip_pre_ex_after: dict[int, str] = field(default_factory=dict)
    tip_initial_period: dict[int, str] = field(default_factory=dict)
    tip_core_disability_definition: dict[int, str] = field(default_factory=dict)
    tip_core_benefit_perc: dict[int, list] = field(default_factory=dict)
    tip_core_benefit_layer: dict[int, list] = field(default_factory=dict)
    tip_core_benefit_perc_additional: dict[int, float] = field(default_factory=dict)
    tip_core_benefit_layer_additional: dict[int, float] = field(default_factory=dict)
    tip_core_overall_maximum: dict[int, float] = field(default_factory=dict)
    tip_employer_waiver: dict[int, float] = field(default_factory=dict)
    tip_employee_waiver: dict[int, float] = field(default_factory=dict)
    tip_hcc_disability_definition: dict[int, str] = field(default_factory=dict)
    tip_hcc_benefit_perc: dict[int, list] = field(default_factory=dict)
    tip_hcc_benefit_layer: dict[int, list] = field(default_factory=dict)
    tip_hcc_overall_maximum: dict[int, float] = field(default_factory=dict)
    tip_include_hcc_waiver: dict[int, str] = field(default_factory=dict)
    tip_hcc_payment_period: dict[int, float] = field(default_factory=dict)
    tip_hcc_payment_type: dict[int, str] = field(default_factory=dict)
    tip_hcc_nominal_amount: dict[int, float] = field(default_factory=dict)
    tip_hcc_escalation_type: dict[int, str] = field(default_factory=dict)
    tip_hcc_escalation_pct: dict[int, float] = field(default_factory=dict)
    tip_payment_period: dict[int, str] = field(default_factory=dict)
    tip_waiver_pct_specified: dict[int, str] = field(default_factory=dict)
    tip_waiver_included: dict[int, str] = field(default_factory=dict)

    education_product: str | None = None
    education_active: dict[int, bool] = field(default_factory=dict)
    education_include_ctc: dict[int, str] = field(default_factory=dict)
    education_max_cover: dict[int, float] = field(default_factory=dict)
    education_nra: dict[int, object] = field(default_factory=dict)
    education_deferred: dict[int, str] = field(default_factory=dict)
    education_deferred_age: dict[int, float] = field(default_factory=dict)
    education_multiple: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CTRBundle:
    ctr_path: Path | None
    ctr_fingerprint: str
    loaded_at: str
    scheme: SchemeRecord
    categories: pl.DataFrame
    members: dict[str, pl.DataFrame]
    active_products: tuple[str, ...]
    active_categories: dict[str, tuple[int, ...]]
    warnings: list[str] = field(default_factory=list)