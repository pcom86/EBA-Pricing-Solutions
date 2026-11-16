
GLA = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "gla",
    "apply_tax_gross_up":    True,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "rate_factors": [
        {"kind": "mortality",   "product": "gla",  "age_col": "age_nb"},
        {"kind": "salary_band", "product": "gla",  "factor": "inc_adj"},
        {"kind": "salary_band", "product": "gla",  "factor": "acaf_adj"},
        {"kind": "industry",    "product": "gla",  "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",    "product": "gla",  "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "trend",       "product": "gla"},
    ],
    "aids_factors": [],
    "add_ons": ["co_premium_gla"],
    "profit_margin_key":     ("gla", "standard"),
    "profit_margin_dc_key":  ("gla", "dc"),
}


LSDB = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "lsdb",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "rate_factors": [
        {"kind": "mortality",         "product": "lsdb", "age_col": "age_nb"},
        {"kind": "salary_band",       "product": "lsdb", "factor": "inc_adj"},
        {"kind": "salary_band",       "product": "lsdb", "factor": "acaf_adj"},
        {"kind": "industry",          "product": "lsdb", "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",          "product": "lsdb", "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "waiting_pd",        "product": "lsdb"},
        {"kind": "baseline_yr",       "product": "lsdb", "factor": "baseline_adj"},
        {"kind": "product_type_adj",  "product": "lsdb"},
    ],

    "aids_factors": [],
    "add_ons": ["co_premium_lsdb"],

    "zero_when_status_in": ("DB",),
    "ineligible_status":   ("DB",),

    "profit_margin_key_by_cover_type": {
        "Multiple": ("lsdb", "standard"),
        "_default": ("lsdb", "dc"),
    },
}

ACCIDENT_DEATH = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "accident_death",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [
        {"kind": "category_max_cover_field", "field": "maximum_cover"},
        {"kind": "cap_by_prior_cover", "prior": "gla"},
    ],

    "rate_factors": [
        {"kind": "mortality",     "product": "adb",  "age_col": "age_nb"},
        {"kind": "salary_factor", "product": "adb"},
    ],

    "aids_factors": [],
    "add_ons": [],
    "profit_margin_key": ("adb", "standard"),
}


ACCIDENT_DISABILITY = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "accident_db",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",
    "zero_when_status_in": ("DB",),

    "cover_caps": [
        {"kind": "category_max_cover_field", "field": "maximum_cover"},
        {"kind": "cap_by_lsdb_or_gla"},
    ],

    "rate_factors": [
        {"kind": "mortality",     "product": "accdb",  "age_col": "age_nb"},
        {"kind": "salary_factor", "product": "accdb"},
    ],

    "aids_factors": [],
    "add_ons": [],
    "profit_margin_key": ("accdb", "standard"),
}


DISMEMBERMENT = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "dismemberment",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",
    "zero_when_status_in": ("DB",),

    "cover_caps": [
        {"kind": "category_max_cover_field", "field": "maximum_cover"},
        {"kind": "cap_by_lsdb_or_gla"},
    ],

    "rate_factors": [
        {"kind": "mortality",     "product": "dism",  "age_col": "age_nb"},
        {"kind": "salary_factor", "product": "dism"},
    ],

    "aids_factors": [],
    "add_ons": [],
    "profit_margin_key": ("dismem", "standard"),
}


TERMINAL = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data_or_gla",
    "cover_column":          "terminal_illness",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [
        {"kind": "category_max_cover_field", "field": "maximum_cover"},
    ],

    "rate_factors": [],
    "aids_factors": [],
    "add_ons": [],

    "special_premium": {
        "kind": "ti_scales_gla",
        "cost_basis_map": {
            "first_year_no_spread":  "first_year",
            "first_year_spread":     "uniform",
            "ongoing":               "ongoing",
        },
    },

    "profit_margin_key": ("ti", "standard"),
}

SPOUSE_GLA = {
    "member_source":         "spouse_dynamic",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "spouse_s_life_cover",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [
        {"kind": "spouse_cover_cap", "prior": "gla",
         "max_multiple_key": ("spouse_gla", "max_multiple")},
        {"kind": "category_max_cover_field", "field": "max_spouse_cover"},
    ],

    "rate_factors_gla_source": [
        {"kind": "mortality_gender_swap", "product": "gla", "age_col": "age_nb"},
        {"kind": "salary_band", "product": "gla", "factor": "inc_adj"},
        {"kind": "salary_band", "product": "gla", "factor": "acaf_adj"},
        {"kind": "industry",    "product": "gla", "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",    "product": "gla", "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "trend",       "product": "gla"},
    ],
    "rate_factors_sp_source": [
        {"kind": "mortality_gender_swap", "product": "gla", "age_col": "age_nb"},
        {"kind": "salary_band", "product": "gla", "factor": "inc_adj"},
        {"kind": "salary_band", "product": "gla", "factor": "acaf_adj"},
        {"kind": "industry",    "product": "gla", "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",    "product": "gla", "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "trend",       "product": "gla"},
    ],

    "aids_factors": [],
    "add_ons": ["co_premium_spouse"],
    "profit_margin_key_by_cover_type": {
    "Cover in member data": ("gla", "dc"),
    "_default":             ("gla", "standard"),
},
"profit_margin_basis": "total_annual",
}


SPOUSE_LSDB = {
    "member_source":         "spouse_dynamic",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "spouse_s_db",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [
        {"kind": "spouse_cover_cap", "prior": "gla",
         "max_multiple_key": ("spouse_lsdb", "max_multiple")},
        {"kind": "cap_by_assumption_limit",
         "product_key": "spouse_lsdb", "param_key": "max_cover"},
    ],

    "rate_factors": [
        {"kind": "mortality",   "product": "spouse", "age_col": "age_nb"},
        {"kind": "salary_band", "product": "spouse", "factor": "inc_adj"},
    ],

    "post_rate_adjust": [
        {"kind": "spouse_factor_multiplicative"},
        {"kind": "spouse_db_definition_multiplicative"},
    ],

    "aids_factors": [],
    "add_ons": [],
    "profit_margin_key_by_cover_type": {
    "Cover in member data": ("gla",  "dc"),
    "_default":             ("lsdb", "standard"),
},
    "profit_margin_basis": "total_annual",
}

CI = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "multiple_or_data",
    "cover_column":          "ci",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [
        {"kind": "category_max_cover_field", "field": "maximum_cover_amount"},
        {"kind": "cap_by_prior_cover", "prior": "gla"},
    ],

    "rate_factors": [
    {"kind": "ci_mortality_dynamic"},
    {"kind": "ci_standalone_loading"},
    {"kind": "ci_industry_loading"},
],

    "aids_factors": [],

    "add_ons": ["co_premium_ci"],

    "profit_margin_key":   ("lsdb", "standard"),
    "profit_margin_basis": "net_risk",
}


FC = {
    "member_source":         "fc_sheet",
    "age_from":              "commencement",
    "cover_rule":            "fc_composite",
    "cover_column":          "fc_cover",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",

    "cover_caps": [],
    "rate_factors": [],
    "aids_factors": [],
    "post_rate_adjust": [],
    "add_ons": [],

    "skip_prb_premium": True,

    "special_premium": {
        "kind": "fc_composite",
        "namibia_rate_cut_global": "namibia_competitive_factor",
        "db_gate_global": "active_selection_db_load",
    },

    "profit_margin_key":   ("gla", "standard"),
    "profit_margin_basis": "net_risk",

    "zero_when_status_in": (),
}

GIP = {
    "member_source":       "gip_sheet",
    "age_from":            "commencement",
    "cover_rule":          "gip_composite",
    "cover_column":        "gip_cover",
    "apply_tax_gross_up":  False,
    "ceasing_source":      "nra_only",
    "nra_source":          "category_or_custom",
    "cover_caps":          [],
    "rate_factors":        [],
    "aids_factors":        [],
    "post_rate_adjust":    [],
    "add_ons":             [],
    "special_premium":     {"kind": "gip_composite"},
    "profit_margin_key":   ("gip", "standard"),
    "profit_margin_basis": "annuity_pv",
    "skip_prb_premium":    True,
    "zero_when_status_in": (),
}

TIP = {
    "member_source":       "tip_sheet",
    "age_from":            "commencement",
    "cover_rule":          "tip_composite",
    "cover_column":        "tip_cover",
    "apply_tax_gross_up":  False,
    "ceasing_source":      "nra_only",
    "nra_source":          "category_or_custom",
    "cover_caps":          [],
    "rate_factors":        [],
    "aids_factors":        [],
    "post_rate_adjust":    [],
    "add_ons":             [],
    "special_premium":     {"kind": "tip_composite"},
    "profit_margin_key":   ("gip", "standard"),
    "profit_margin_basis": "annuity_pv",
    "skip_prb_premium":    True,
    "zero_when_status_in": (),
}


EDUCATION_GUARDIAN = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "education_guardian_composite",
    "cover_column":          "education_guardian_cover",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",
    "rate_factors": [
        {"kind": "mortality",   "product": "gla", "age_col": "age_nb"},
        {"kind": "salary_band", "product": "gla", "factor": "inc_adj"},
        {"kind": "salary_band", "product": "gla", "factor": "acaf_adj"},
        {"kind": "industry",    "product": "gla", "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",    "product": "gla", "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "trend",       "product": "gla"},
    ],
    "aids_factors": [],
    "special_premium": {"kind": "education_guardian_composite"},
    "profit_margin_key": ("gla", "standard"),
}


EDUCATE_A_CHILD = {
    "member_source":         "gla_sheet",
    "age_from":              "commencement",
    "cover_rule":            "educate_a_child_composite",
    "cover_column":          "education_cover",
    "apply_tax_gross_up":    False,
    "ceasing_source":        "deferred_or_nra",
    "nra_source":            "category_or_custom",
    "rate_factors": [
        {"kind": "mortality",   "product": "gla", "age_col": "age_nb"},
        {"kind": "salary_band", "product": "gla", "factor": "inc_adj"},
        {"kind": "salary_band", "product": "gla", "factor": "acaf_adj"},
        {"kind": "industry",    "product": "gla", "factor": "inc_adj",  "by_scheme_size": True},
        {"kind": "industry",    "product": "gla", "factor": "acaf_adj", "by_scheme_size": True},
        {"kind": "trend",       "product": "gla"},
    ],
    "aids_factors": [],
    "special_premium": {"kind": "educate_a_child_composite"},
    "profit_margin_key": ("gla", "standard"),
}


RECIPES = {
    "gla":                 GLA,
    "lsdb":                LSDB,
    "accident_death":      ACCIDENT_DEATH,
    "accident_disability": ACCIDENT_DISABILITY,
    "dismemberment":       DISMEMBERMENT,
    "terminal":            TERMINAL,
    "spouse_gla":          SPOUSE_GLA,
    "spouse_lsdb":         SPOUSE_LSDB,
    "ci":                  CI,
    "fc":                  FC,
    "gip":                 GIP,
    "tip":                 TIP,
    "education_guardian":  EDUCATION_GUARDIAN,
    "educate_a_child":     EDUCATE_A_CHILD,
}