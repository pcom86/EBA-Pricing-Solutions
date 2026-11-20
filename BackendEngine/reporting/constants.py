
CB_PRODUCTS = [
    "fc",
    "gla",
    "lsdb",
    "ci",
    "spouse_gla",
    "spouse_lsdb",
    "terminal",
    "accident_death",
    "accident_disability",
    "dismemberment",
]

CB_RENDER_ORDER = [
    "gla", "lsdb", "ci", "spouse_gla", "spouse_lsdb", "terminal",
    "accident_death", "accident_disability", "dismemberment",
    "education", "fc", "gip", "tip",
]


CB_SECTION_TITLE = {
    'tip': 'Temporary Income Protection',
    'gip': 'Group Income Protection',
    "fc":                  "Family Cover",
    "gla":                 "Life Assurance",
    "lsdb":                "Lump Sum Disability",
    "ci":                  "Critical Incidents",
    "spouse_gla":          "Spouses Life Cover",
    "spouse_lsdb":         "Spouses Disability Cover",
    "terminal":            "Terminal Illness Cover",
    "accident_death":      "Accidental Death",
    "accident_disability": "Accidental Disability",
    "dismemberment":       "Dismemberment",
    "education":           "Educate a Child",
}

CB_LABEL_SUFFIX = {
    'tip': 'TIP',
    'gip': 'GIP',
    "fc":                  "FC",
    "gla":                 "GLA",
    "lsdb":                "LSDB",
    "ci":                  "CI",
    "spouse_gla":          "SP GLA",
    "spouse_lsdb":         "SP LSDB",
    "terminal":            "TIC",
    "accident_death":      "ACCDTH",
    "accident_disability": "ACCDB",
    "dismemberment":       "DISMEM",
    "education":           "EDU",
}


CB_ADDONS = {
    'tip': [('Escalation', 'escalation')],
    'gip': [('Escalation', 'escalation'), ('HCC Waiver', 'waiver_premium'), ('Conversion Option ', 'co_premium')],
    "fc": [
        ("Cover to Continue",  "ctc_premium"),
        ("Paid-Up Cover",      "pup"),
        ("PRO Premium ",       "prb_premium"),
    ],
    "gla": [
        ("Conversion Option ",   "co_premium"),
        ("Cover to Continue",    "ctc_premium"),
        ("Waiver of Premium",    "waiver_premium"),
        ("PRO Premium ",         "prb_premium"),
    ],
    "lsdb": [
        ("Conversion Option ",   "co_premium"),
        ("PRO Premium ",         "prb_premium"),
    ],
    "ci": [
        ("Conversion Option ",   "co_premium"),
        ("Reinstatement Premium", "reinstatement_premium"),
        ("PRO Premium ",         "prb_premium"),
    ],
    "spouse_gla": [
        ("Conversion Option ",   "co_premium"),
        ("Cover to Continue",    "ctc_premium"),
        ("PRO Premium ",         "prb_premium"),
    ],
    "spouse_lsdb":         [("PRO Premium ", "prb_premium")],
    "terminal":            [("PRO Premium ", "prb_premium")],
    "accident_death":      [("PRO Premium ", "prb_premium")],
    "accident_disability": [("PRO Premium ", "prb_premium")],
    "dismemberment":       [("PRO Premium ", "prb_premium")],
    "education": [
        ("Cover to Continue", "ctc_premium"),
        ("PRO Premium ",      "prb_premium"),
    ],
}

N_CATEGORIES = 20

DEFAULT_COMMISSION_PCT = 0.075
DEFAULT_VAT_PCT = 0.15

PO_PRODUCTS = [
    "gla", "lsdb", "ci",
    "spouse_gla", "spouse_lsdb",
    "terminal",
    "accident_death", "accident_disability", "dismemberment",
]

PO_PRODUCT_DISPLAY = {
    "education":           "Education Guardian",
    "tip":                 "TIP",
    "gip":                 "GIP",
    "fc":                  "Family Cover",
    "gla":                 "GLA",
    "lsdb":                "LSDB Standalone",
    "ci":                  "CI Standalone",
    "spouse_gla":          "Spouses - GLA",
    "spouse_lsdb":         "Spouses - LSDB",
    "terminal":            "Terminal Illness Cover",
    "accident_death":      "Accidental Death",
    "accident_disability": "Accidental Disability",
    "dismemberment":       "Dismemberment",
}

PO_BUILDUP_TITLE = {
    "education":           "Premium Build-Up: Education Guardian",
    "tip":                 "Premium Build-Up: Temporary Income Protection",
    "gip":                 "Premium Build-Up: Group Income Protection",
    "fc":                  "Premium Build-Up: Family Cover",
    "gla":                 "Premium Build-Up: GLA",
    "lsdb":                "Premium Build-Up: LSDB",
    "ci":                  "Premium Build-Up: CI",
    "spouse_gla":          "Premium Build-Up: Spouse GLA",
    "spouse_lsdb":         "Premium Build-Up: Spouse LSDB",
    "terminal":            "Premium Build-Up: Terminal Illness Cover",
    "accident_death":      "Premium Build-Up: Accidental Death",
    "accident_disability": "Premium Build-Up: Accidental Disability",
    "dismemberment":       "Premium Build-Up: Dismemberment",
}

PO_TECH_TITLE = {
    "education":           "Education Guardian - Tech Rates",
    "tip":                 "TIP - Tech Rates",
    "gip":                 "GIP - Tech Rates",
    "fc":                  "FC - Tech Rates",
    "gla":                 "GLA - Tech Rates (Default)",
    "lsdb":                "LSDB - Tech Rates",
    "ci":                  "CI - Tech Rates",
    "spouse_gla":          "Spouse GLA - Tech Rates",
    "spouse_lsdb":         "Spouse LSDB - Tech Rates",
    "terminal":            "TI Cover - Tech Rates",
    "accident_death":      "AccDTH - Tech Rates",
    "accident_disability": "AccDB - Tech Rates",
    "dismemberment":       "Dismemberment - Tech Rates",
}
PO_BASIS_TITLE = {k: v.replace("Tech Rates", "100% Basis").replace(" (Default)", "")
                  for k, v in PO_TECH_TITLE.items()}

PO_SALARY_LABEL = {
    "education":           "% of Salary ",
    "tip":                 "% of Salary ",
    "gip":                 "% of Salary ",
    "fc":                  "% of Salary ",
    "gla":                 "% of GLA Salary",
    "lsdb":                "% of Benefit Salary",
    "ci":                  "% of Benefit Salary",
    "spouse_gla":          "% of Benefit Salary",
    "spouse_lsdb":         "% of Benefit Salary",
    "terminal":            "% of GLA Salary",
    "accident_death":      "% of GLA Salary",
    "accident_disability": "% of GLA Salary",
    "dismemberment":       "% of GLA Salary",
}
PO_RATE_LABEL = {
    "education":           "Rand Per Member per Month",
    "tip":                 "Rand Per Member per Month",
    "gip":                 "Rand Per Member per Month",
    "fc":                  "Rand Per Member per Month",
    "gla":                 "Monthly Rate per R1000 Cover",
    "lsdb":                "Monthly Rate per R1000 Benefit Cover",
    "ci":                  "Monthly Rate per R1000 Benefit Cover",
    "spouse_gla":          "Monthly Rate per R1000 Benefit Cover",
    "spouse_lsdb":         "Monthly Rate per R1000 Benefit Cover",
    "terminal":            "Monthly Rate per R1000 Benefit Cover",
    "accident_death":      "Monthly Rate per R1000 Benefit Cover",
    "accident_disability": "Monthly Rate per R1000 Benefit Cover",
    "dismemberment":       "Monthly Rate per R1000 Benefit Cover",
}

PO_TECH_ADDONS = {
    "education": [("Cover to Continue", "ctc_premium"), ("PRO Premium ", "prb_premium")],
    "tip": [],
    "gip": [("Conversion Option ", "co_premium"),
            ("HCC Waiver", "waiver_premium")],
    "fc": [("Cover to Continue", "ctc_premium"),
           ("Paid-Up Cover", "waiver_premium"),
           ("PRO Premium ", "prb_premium")],
    "gla":                 [("Conversion Option ", "co_premium"),
                            ("Cover to Continue",  "ctc_premium"),
                            ("Waiver of Premium",  "waiver_premium"),
                            ("PRO Premium ",       "prb_premium")],
    "lsdb":                [("Conversion Option ", "co_premium"),
                            ("PRO Premium ",       "prb_premium")],
    "ci":                  [("Conversion Option ",   "co_premium"),
                            ("Reinstatement Premium","reinstatement_premium"),
                            ("PRO Premium ",         "prb_premium")],
    "spouse_gla":          [("Conversion Option ", "co_premium"),
                            ("Cover to Continue",  "ctc_premium"),
                            ("PRO Premium ",       "prb_premium")],
    "spouse_lsdb":         [("PRO Premium ", "prb_premium")],
    "terminal":            [("PRO Premium ", "prb_premium")],
    "accident_death":      [("PRO Premium ", "prb_premium")],
    "accident_disability": [("PRO Premium ", "prb_premium")],
    "dismemberment":       [("PRO Premium ", "prb_premium")],
}
