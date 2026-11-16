from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import polars as pl

RatingType = Literal["yes_no", "accel_standalone"]


@dataclass(frozen=True)
class ProductSpec:
    key: str
    display_name: str

    rating_label: str
    pricing_basis_label: str
    fees_priced_label: str
    basic_fee_label: str
    rating_type: RatingType

    catinput_blocks: dict[str, tuple[int, int]]

    member_source: str
    cover_column: str

    basis_mortality: tuple[str, ...] = ()
    basis_salary_band: tuple[str, ...] = ()
    basis_industry: tuple[str, ...] = ()
    basis_regional: tuple[str, ...] = ()
    basis_trend: tuple[str, ...] = ()
    basis_waiting_periods: tuple[str, ...] = ()
    basis_product_type_adj: tuple[str, ...] = ()
    basis_education_guardian_cover: tuple[str, ...] = ()
    basis_education_guardian_salary_bands: tuple[str, ...] = ()
    uses_ti_factors: bool = False

    assumption_limits: tuple[str, ...] = ()
    assumption_margins: tuple[str, ...] = ()
    assumption_co: tuple[str, ...] = ()
    uses_lsdb_features: bool = False
    uses_spouse_factors: bool = False
    uses_ci_table_mapping: bool = False
    uses_ci_reinstatement: bool = False
    uses_ctc_options: bool = False
    uses_tax_tables: bool = False
    uses_fc_rates: bool = False
    uses_fc_demographics: bool = False
    uses_fc_family_composition: bool = False
    uses_fc_db_loading: bool = False
    uses_ext_fc_base_rates: bool = False
    uses_ext_fc_salary_factors: bool = False
    uses_ext_fc_basis_params: bool = False
    uses_ext_fc_assumed_age_dist: bool = False
    uses_gip_wp_loading: bool = False
    uses_gip_annuity_factors: bool = False
    uses_gip_esc_loading: bool = False
    uses_gip_nra_interp: bool = False
    uses_gip_wp_annuity_mapping: bool = False
    uses_gip_wp_base_inceptions: bool = False
    uses_gip_backdating_loading: bool = False
    uses_gip_rr_loading: bool = False
    uses_gip_ip_loading: bool = False
    uses_gip_db_loading: bool = False
    uses_gip_annuity_salary_bands: bool = False
    uses_gip_annuity_age_bands: bool = False
    uses_gip_hcc_basis: bool = False
    uses_gip_fee_table: bool = False
    uses_wp_categories: bool = False
    uses_ext_fc_params: bool = False
    uses_gip_wp_loading: bool = False
    uses_gip_rr_loading: bool = False
    uses_gip_wp_base_inceptions: bool = False
    uses_gip_annuity_salary_bands: bool = False
    uses_gip_ip_loading: bool = False
    uses_tip_annuity_factors: bool = False
    uses_tip_payment_period_map: bool = False
    uses_tip_age_interp: bool = False
    uses_tip_esc_loading: bool = False
    uses_tip_wp_ann_map: bool = False
    uses_tip_db_loading: bool = False
    uses_education_guardian_table: bool = False

PRODUCT_REGISTRY: dict[str, ProductSpec] = {
    "gla": ProductSpec(
        key="gla", display_name="Group Life Assurance",
        rating_label="GLA", pricing_basis_label="GLA",
        fees_priced_label="GLA", basic_fee_label="GLA",
        rating_type="yes_no",
        catinput_blocks={"default": (17, 31)},
        member_source="Member Data - GLA", cover_column="gla_cover",
        basis_mortality=("gla", "gla_aids"),
        basis_salary_band=("gla",),
        basis_industry=("gla",),
        basis_trend=("gla",),
        assumption_limits=("gla",),
        assumption_margins=("gla",),
        assumption_co=("gla",),
        uses_tax_tables=True,
    ),
    "lsdb": ProductSpec(
        key="lsdb", display_name="Lump Sum Disability Benefit",
        rating_label="LSDB", pricing_basis_label="LSDB",
        fees_priced_label="LSDB", basic_fee_label="LSDB",
        rating_type="accel_standalone",
        catinput_blocks={"accelerator": (31, 47), "standalone": (75, 91)},
        member_source="Member Data - GLA", cover_column="lsdb_cover",
        basis_mortality=("lsdb",),
        basis_salary_band=("lsdb",),
        basis_industry=("lsdb",),
        basis_trend=("lsdb",),
        basis_waiting_periods=("lsdb",),
        basis_product_type_adj=("lsdb",),
        assumption_limits=("lsdb",),
        assumption_margins=("lsdb",),
        assumption_co=("lsdb",),
        uses_lsdb_features=True,
    ),
    "ci": ProductSpec(
        key="ci", display_name="Critical Illness",
        rating_label="CI", pricing_basis_label="CI",
        fees_priced_label="CI", basic_fee_label="CI",
        rating_type="accel_standalone",
        catinput_blocks={"accelerator": (47, 60), "standalone": (91, 103)},
        member_source="Member Data - GLA", cover_column="ci_cover",
        basis_mortality=(),
        basis_salary_band=("ci", "ci_aids_acc"),
        basis_industry=("ci", "ci_acc", "ci_std", "ci_new", "ci_old"),
        basis_regional=("ci",),
        assumption_limits=("ci", "ci_existing", "ci_new"),
        assumption_margins=("ci","lsdb"),
        assumption_co=("ci",),
        uses_ci_table_mapping=True,
        uses_ci_reinstatement=True,
    ),
    "spouse_gla": ProductSpec(
        key="spouse_gla", display_name="Spouse GLA",
        rating_label="Spouse - GLA", pricing_basis_label="Spouses - GLA",
        fees_priced_label="Spouses - GLA", basic_fee_label="Spouse - GLA",
        rating_type="yes_no",
        catinput_blocks={"default": (60, 68)},
        member_source="Member Data - SP", cover_column="spouse_gla_cover",
        basis_mortality=("gla", "gla_aids"),
        basis_salary_band=("gla",),
        basis_industry=("gla",),
        basis_trend=("gla",),
        basis_regional=("spouse",),
        assumption_limits=("spouse_gla",),
        assumption_margins=("gla",),
        assumption_co=("spouse",),
        uses_spouse_factors=True,
        uses_ctc_options=True,
    ),
    "spouse_lsdb": ProductSpec(
        key="spouse_lsdb", display_name="Spouse LSDB",
        rating_label="Spouse - LSDB", pricing_basis_label="Spouses - LSDB",
        fees_priced_label="Spouses - LSDB", basic_fee_label="Spouse - LSDB",
        rating_type="yes_no",
        catinput_blocks={"default": (68, 75)},
        member_source="Member Data - SP", cover_column="spouse_lsdb_cover",
        basis_mortality=("spouse", "spouse_aids"),
        basis_salary_band=("spouse", "spouse_aids"),
        basis_industry=("spouse",),
        basis_regional=("spouse",),
        assumption_limits=("spouse_lsdb",),
        assumption_margins=("lsdb","gla"),
        assumption_co=("spouse",),
        uses_spouse_factors=True,
        uses_ctc_options=True,
    ),
    "terminal": ProductSpec(
        key="terminal", display_name="Terminal Illness Cover",
        rating_label="Terminal Illness Cover",
        pricing_basis_label="Terminal Illness Cover",
        fees_priced_label="Terminal Illness Cover",
        basic_fee_label="Terminal Illness Cover",
        rating_type="yes_no",
        catinput_blocks={"default": (103, 111)},
        member_source="Member Data - GLA", cover_column="terminal_illness",
        uses_ti_factors=True,
        assumption_limits=("ti",),
        assumption_margins=("ti",),
    ),
    "accident_death": ProductSpec(
        key="accident_death", display_name="Accidental Death",
        rating_label="Accidental Death", pricing_basis_label="Accidental Death",
        fees_priced_label="Accidental Death", basic_fee_label="Accidental Death",
        rating_type="yes_no",
        catinput_blocks={"default": (111, 116)},
        member_source="Member Data - GLA", cover_column="accident_death",
        basis_mortality=("adb",),
        basis_salary_band=("adb",),
        assumption_limits=("adb",),
        assumption_margins=("adb",),
    ),
    "accident_disability": ProductSpec(
        key="accident_disability", display_name="Accidental Disability",
        rating_label="Accidental Disability",
        pricing_basis_label="Accidental Disability",
        fees_priced_label="Accidental Disability",
        basic_fee_label="Accidental Disability",
        rating_type="yes_no",
        catinput_blocks={"default": (116, 121)},
        member_source="Member Data - GLA", cover_column="accident_disability",
        basis_mortality=("accdb",),
        basis_salary_band=("accdb",),
        assumption_limits=("accdb",),
        assumption_margins=("accdb",),
    ),
    "dismemberment": ProductSpec(
        key="dismemberment", display_name="Dismemberment",
        rating_label="Dismemberment", pricing_basis_label="Dismemberment",
        fees_priced_label="Dismemberment", basic_fee_label="Dismemberment",
        rating_type="yes_no",
        catinput_blocks={"default": (121, 126)},
        member_source="Member Data - GLA", cover_column="dismemberment",
        basis_mortality=("dism",),
        basis_salary_band=("dism",),
        assumption_limits=("dismem",),
        assumption_margins=("dismem",),
    ),
    "fc": ProductSpec(
        key="fc", display_name="Family Cover",
        rating_label="FC",
        pricing_basis_label="FC",
        fees_priced_label="FC",
        basic_fee_label="FC",
        rating_type="yes_no",
        catinput_blocks={"default": (135, 154)},
        member_source="Member Data - FC",
        cover_column="fc_cover",
        basis_mortality=("gla", "gla_aids"),
        basis_salary_band=("gla",),
        basis_industry=("gla",),
        basis_trend=("gla",),
        assumption_limits=("fc",),
        assumption_margins=("gla",),
        uses_fc_rates=True,
        uses_fc_demographics=True,
        uses_fc_family_composition=True,
        uses_fc_db_loading=True,
    ),
    "ext_fc": ProductSpec(
        key="ext_fc", display_name="Extended Family Cover",
        rating_label="Ext FC",
        pricing_basis_label="Ext FC",
        fees_priced_label="Ext FC",
        basic_fee_label="Ext FC",
        rating_type="yes_no",
        catinput_blocks={"default": (242, 252)},
        member_source="Member Data - Ext FC",
        cover_column="ext_fc_cover",
        uses_ext_fc_base_rates=True,
        uses_ext_fc_salary_factors=True,
        uses_ext_fc_basis_params=True,
        uses_ext_fc_assumed_age_dist=True,
        assumption_margins=("gla",),
        uses_ext_fc_params=True,
    ),
    "gip": ProductSpec(
        key="gip", display_name="Group Income Protection",
        rating_label="GIP", pricing_basis_label="GIP",
        fees_priced_label="GIP", basic_fee_label="GIP",
        rating_type="yes_no",
        catinput_blocks={"default": (154, 158)},
        member_source="Member Data - GIP",
        cover_column="gip_cover",
        basis_mortality=("gip",),
        basis_salary_band=("gip",),
        basis_industry=("gip",),
        assumption_limits=("gip",),
        assumption_margins=("gip",),
        assumption_co=("gip",),
        uses_tax_tables=True,
        uses_gip_wp_loading=True,
        uses_gip_annuity_factors=True,
        uses_gip_esc_loading=True,
        uses_gip_nra_interp=True,
        uses_gip_wp_annuity_mapping=True,
        uses_gip_wp_base_inceptions=True,
        uses_gip_backdating_loading=True,
        uses_gip_rr_loading=True,
        uses_gip_ip_loading=True,
        uses_gip_db_loading=True,
        uses_gip_annuity_salary_bands=True,
        uses_gip_annuity_age_bands=True,
        uses_gip_hcc_basis=True,
        uses_gip_fee_table=True,
        uses_wp_categories=True,
    ),
    "tip": ProductSpec(
        key="tip", display_name="Temporary Income Protection",
        rating_label="TIP", pricing_basis_label="TIP",
        fees_priced_label="TIP", basic_fee_label="TIP",
        rating_type="yes_no",
        catinput_blocks={"default": (197, 201)},
        member_source="Member Data - TIP",
        cover_column="tip_cover",
        basis_mortality=("gip",),
        basis_salary_band=("gip",),
        basis_industry=("gip",),
        assumption_limits=("gip",),
        assumption_margins=("gip",),
        uses_tax_tables=True,
        uses_gip_wp_loading=True,
        uses_gip_rr_loading=True,
        uses_gip_wp_base_inceptions=True,
        uses_gip_annuity_salary_bands=True,
        uses_gip_ip_loading=True,
        uses_tip_annuity_factors=True,
        uses_tip_payment_period_map=True,
        uses_tip_age_interp=True,
        uses_tip_esc_loading=True,
        uses_tip_wp_ann_map=True,
        uses_tip_db_loading=True,
    ),
    "educate_a_child": ProductSpec(
        key="educate_a_child", display_name="Educate a Child",
        rating_label="Educate a Child",
        pricing_basis_label="Educate a Child",
        fees_priced_label="Educate a Child",
        basic_fee_label="Educate a Child",
        rating_type="yes_no",
        catinput_blocks={"default": (131, 135)},
        member_source="Member Data - GLA", cover_column="education_cover",
        basis_mortality=("gla", "gla_aids"),
        basis_salary_band=("gla",),
        basis_industry=("gla",),
        basis_trend=("gla",),
        assumption_margins=("gla",),
        uses_tax_tables=False,
    ),
    "education_guardian": ProductSpec(
        key="education_guardian", display_name="Education Guardian",
        rating_label="Education Guardian",
        pricing_basis_label="Education Guardian",
        fees_priced_label="Education Guardian",
        basic_fee_label="Education Guardian",
        rating_type="yes_no",
        catinput_blocks={"default": (131, 135)},
        member_source="Member Data - GLA", cover_column="education_guardian_cover",
        basis_mortality=("gla", "gla_aids"),
        basis_salary_band=("gla",),
        basis_industry=("gla",),
        basis_trend=("gla",),
        assumption_margins=("gla",),
        assumption_limits=("education_guardian",),
        uses_tax_tables=False,
        uses_education_guardian_table=True,
        basis_education_guardian_cover=("education_guardian",),
        basis_education_guardian_salary_bands=("education_guardian",),
    ),    

}

IN_SCOPE_PRODUCTS: tuple[str, ...] = tuple(PRODUCT_REGISTRY.keys())


def get(product_key: str) -> ProductSpec:
    if product_key not in PRODUCT_REGISTRY:
        raise KeyError(f"Product '{product_key}' not registered. Available: {list(PRODUCT_REGISTRY)}")
    return PRODUCT_REGISTRY[product_key]


def resolve_ci_mortality_table(ci_table_mapping: pl.DataFrame,
                                product_option: str,
                                variant: str) -> dict:
    row = ci_table_mapping.filter(pl.col("product_option") == product_option)
    if row.height == 0:
        raise KeyError(f"CI product_option '{product_option}' not in ci_table_mapping. "
                       f"Available: {sorted(ci_table_mapping['product_option'].unique().to_list())}")
    row = row.row(0, named=True)
    col = "accelerator_table" if variant == "accelerator" else "standalone_table"
    raw_name = str(row[col])
    name = raw_name.lower()
    if name.endswith("_accelerator"):
        name = name[:-len("_accelerator")] + "_acc"
    elif name.endswith("_standalone"):
        name = name[:-len("_standalone")] + "_std"
    else:
        name = name + "_acc"
    return {
        "table_key": name,
        "age_definition": row["age_definition"],
        "variant_type": row["variant_type"],
    }