"""Wetsparameters box 3. Alleen constanten; Lobke controleert de waarden zelf."""

PARAMETERS_STAND = "2026-10-07"

# Wetsvoorstel Wet werkelijk rendement box 3 (niet definitief). Aan- en verkoopkosten zijn aftrekbaar.
WWR_TARIEF = 0.36
WWR_HEFFINGSVRIJ_RESULTAAT = 1800      # per persoon
WWR_VERLIESDREMPEL = 500               # alleen verlies boven dit bedrag is verrekenbaar

# Huidig stelsel (forfaitair, Overbruggingswet box 3) per jaar; definitief=False: (deels) voorlopige percentages.
FORFAITAIR_EERSTE_JAAR = 2023
FORFAITAIR = {
    2023: {"bank": 0.0092, "overig": 0.0617, "schuld": 0.0246, "heffingsvrij": 57000, "schuldendrempel": 3400, "tarief": 0.32, "definitief": True},
    2024: {"bank": 0.0144, "overig": 0.0604, "schuld": 0.0261, "heffingsvrij": 57000, "schuldendrempel": 3700, "tarief": 0.36, "definitief": True},
    2025: {"bank": 0.0137, "overig": 0.0588, "schuld": 0.0270, "heffingsvrij": 57684, "schuldendrempel": 3800, "tarief": 0.36, "definitief": True},
    2026: {"bank": 0.0128, "overig": 0.0600, "schuld": 0.0270, "heffingsvrij": 59357, "schuldendrempel": 3800, "tarief": 0.36, "definitief": False},
}
