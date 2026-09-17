"""Reference data and defect specification for the synthetic retail bank.

Everything that makes the generated data *look* like a UK retail bank lives here, separate from the
mechanics of generating it. Two reasons: the numbers below are the ones a reviewer will argue with,
so they should be readable in one screen; and the defect table is the contract between the generator
and the tests, so it has to be data rather than code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------------
# Merchant category codes. amount_mu / amount_sigma parameterise a lognormal in GBP minor units,
# chosen so the median basket for each category is roughly plausible for a UK consumer.
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MccSpec:
    mcc: int
    category: str
    weight: float
    amount_mu: float       # lognormal mu over amount in pence
    amount_sigma: float
    online_share: float


MCCS: tuple[MccSpec, ...] = (
    MccSpec(5411, "groceries",           0.210, 7.60, 0.75, 0.10),
    MccSpec(5812, "eating_out",          0.140, 7.30, 0.70, 0.18),
    MccSpec(5814, "fast_food",           0.110, 6.60, 0.55, 0.30),
    MccSpec(5541, "fuel",                0.060, 8.30, 0.45, 0.02),
    MccSpec(4111, "transport",           0.085, 6.20, 0.80, 0.55),
    MccSpec(5912, "pharmacy",            0.035, 6.90, 0.65, 0.08),
    MccSpec(5691, "clothing",            0.060, 8.10, 0.85, 0.45),
    MccSpec(5732, "electronics",         0.025, 9.40, 0.95, 0.60),
    MccSpec(7995, "gambling",            0.012, 7.90, 1.20, 0.95),
    MccSpec(4816, "digital_services",    0.070, 6.40, 0.60, 1.00),
    MccSpec(4900, "utilities",           0.045, 8.60, 0.55, 0.90),
    MccSpec(6011, "atm_withdrawal",      0.040, 9.20, 0.50, 0.00),
    MccSpec(5944, "jewellery",           0.008, 9.80, 1.00, 0.40),
    MccSpec(7011, "hotels",              0.020, 9.50, 0.80, 0.75),
    MccSpec(4722, "travel_agents",       0.015, 10.10, 0.90, 0.85),
    MccSpec(8062, "healthcare",          0.010, 8.80, 0.90, 0.20),
    MccSpec(5999, "misc_retail",         0.055, 7.50, 0.90, 0.35),
)

CURRENCIES: tuple[tuple[str, float, float], ...] = (
    # code, share of transactions, approximate GBP per unit
    ("GBP", 0.930, 1.0000),
    ("EUR", 0.032, 0.8550),
    ("USD", 0.026, 0.7900),
    ("SEK", 0.005, 0.0730),
    ("AED", 0.004, 0.2150),
    ("JPY", 0.003, 0.0052),
)

SEGMENTS: tuple[tuple[str, float, float], ...] = (
    # name, share of customers, relative spend multiplier
    ("mass",     0.62, 1.00),
    ("student",  0.14, 0.55),
    ("affluent", 0.19, 1.85),
    ("premier",  0.05, 3.40),
)

PRODUCTS: tuple[tuple[str, float], ...] = (
    ("CURRENT", 0.70),
    ("SAVER", 0.18),
    ("STUDENT", 0.08),
    ("PREMIER", 0.04),
)

CHANNELS: tuple[str, ...] = ("chip_and_pin", "contactless", "ecommerce", "atm", "mail_order")
TXN_TYPES: tuple[str, ...] = ("purchase", "refund", "atm_withdrawal", "fee")
CARD_STATUSES: tuple[tuple[str, float], ...] = (("active", 0.90), ("blocked", 0.03), ("expired", 0.05), ("cancelled", 0.02))
KYC_STATUSES: tuple[tuple[str, float], ...] = (("verified", 0.93), ("pending", 0.04), ("referred", 0.02), ("rejected", 0.01))
RISK_BANDS: tuple[tuple[str, float], ...] = (("low", 0.82), ("medium", 0.15), ("high", 0.03))

UK_CITIES: tuple[tuple[str, str], ...] = (
    ("London", "E1"), ("London", "SW11"), ("London", "N4"), ("Manchester", "M1"), ("Birmingham", "B3"),
    ("Leeds", "LS1"), ("Glasgow", "G1"), ("Bristol", "BS1"), ("Sheffield", "S1"), ("Liverpool", "L1"),
    ("Edinburgh", "EH1"), ("Cardiff", "CF10"), ("Belfast", "BT1"), ("Newcastle", "NE1"), ("Nottingham", "NG1"),
    ("Brighton", "BN1"), ("Leicester", "LE1"), ("Reading", "RG1"), ("Oxford", "OX1"), ("Cambridge", "CB1"),
)

MERCHANT_PREFIXES: tuple[str, ...] = (
    "Northgate", "Riverside", "Kings", "Acorn", "Beacon", "Clearwater", "Dovetail", "Elmwood", "Foxglove",
    "Granary", "Harbour", "Ivywell", "Junction", "Kestrel", "Lantern", "Maypole", "Norfolk", "Orchard",
    "Pinehill", "Quarry", "Redbrick", "Stonecross", "Thornbury", "Upton", "Vesper", "Westbrook",
)
MERCHANT_SUFFIXES: tuple[str, ...] = (
    "Stores", "Group", "Retail", "Trading", "& Co", "Markets", "Direct", "Online", "Services", "Limited",
)

# Weekday multipliers, Monday first. Card spend peaks Friday and Saturday.
WEEKDAY_FACTOR: tuple[float, ...] = (0.88, 0.86, 0.92, 1.02, 1.28, 1.34, 1.05)

# Month multipliers, January first. December is the retail peak, January the trough.
MONTH_FACTOR: tuple[float, ...] = (0.86, 0.88, 0.97, 1.00, 1.03, 1.02, 1.05, 1.04, 0.99, 1.02, 1.08, 1.31)


# --------------------------------------------------------------------------------------------
# Defects. Each row is injected at the stated rate and recorded in the run manifest, so the day-2
# rule engine and the day-5 benchmark can both assert exact counts rather than "roughly some".
# `dimension` uses the DAMA data quality dimensions so the eventual scorecard rolls up cleanly.
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DefectSpec:
    code: str
    feed: str
    dimension: str
    rate: float
    description: str


DEFECTS: tuple[DefectSpec, ...] = (
    DefectSpec("D01", "card_transactions", "completeness", 0.0040, "merchant_id nulled on a purchase, which should always carry one"),
    DefectSpec("D02", "card_transactions", "uniqueness",   0.0015, "transaction_id duplicated, the row emitted twice"),
    DefectSpec("D03", "card_transactions", "consistency",  0.0020, "card_id points at a card that does not exist in the cards extract"),
    DefectSpec("D04", "card_transactions", "validity",     0.0010, "purchase carries a negative amount_minor"),
    DefectSpec("D05", "card_transactions", "validity",     0.0008, "mcc outside the ISO 18245 range"),
    DefectSpec("D06", "card_transactions", "timeliness",   0.0060, "posted_date lands 3-20 days after authorisation"),
    DefectSpec("D07", "card_transactions", "accuracy",     0.0005, "amount_minor inflated by a factor of 100, a minor/major unit slip"),
    DefectSpec("D08", "customers",         "validity",     0.0090, "postcode does not match the UK format"),
    DefectSpec("D09", "customers",         "completeness", 0.0060, "kyc.verified_at missing while kyc.status is verified"),
    DefectSpec("D10", "accounts",          "consistency",  0.0040, "closed_date present while status is open"),
    DefectSpec("D11", "fx_rates",          "completeness", 0.0000, "whole rate day missing, on fixed calendar gaps"),
)

FX_GAP_EVERY_N_DAYS: int = 29  # deterministic gaps so the as-of join has something to prove


@dataclass
class ScaleProfile:
    """One generation size. `dev` is what you iterate on, `bench` is what the day-5 numbers quote."""

    name: str
    customers: int
    merchants: int
    days: int
    txns_per_customer_per_day: float
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def approx_transactions(self) -> int:
        return int(self.customers * self.days * self.txns_per_customer_per_day)


PROFILES: dict[str, ScaleProfile] = {
    # smoke: runs in under a second, used by the test suite in CI
    "smoke": ScaleProfile("smoke", customers=200, merchants=150, days=30, txns_per_customer_per_day=1.2),
    # dev: what you iterate against. Roughly half a million transactions, generates in under 10s
    "dev": ScaleProfile("dev", customers=5_000, merchants=2_000, days=90, txns_per_customer_per_day=1.2),
    # bench: the dataset every published number is measured on. ~50M transactions over one year
    "bench": ScaleProfile("bench", customers=120_000, merchants=40_000, days=365, txns_per_customer_per_day=1.15),
}
