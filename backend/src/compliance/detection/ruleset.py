"""The rule catalogue for Family B (typologies) and Family C (rings).

Family A asks "is this number unusual?" and answers with a fitted statistic.
Families B and C ask "does this match a known pattern?" and answer with a
**rule**: a named test with explicit numeric parameters. That difference is
why they need a different tuning surface. A z-score threshold is one slider;
a structuring rule is a band, a count and a window, and moving any one of them
changes what the rule means.

Rules are declared as **templates** with typed parameters, not as code with
constants baked in. Two things follow:

* The tuning screen renders controls from the template, so a rule cannot ship
  with parameters that have no way to be changed.
* A compliance officer can add *their own* rule by instantiating a template
  with different parameters and a scope — "structuring, but HKD 100,000 and
  only for MCC 5944". The instance is data, so it needs no deploy.

What is deliberately **not** offered is a free-text expression language.
Analyst-authored predicates evaluated at runtime would be an arbitrary code
path into the detection engine, and an AML rule that nobody can statically
review is not auditable. Templates keep every rule explainable by construction:
the reason code names the template and the parameters that were in force.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Family(str, Enum):
    B = "B"
    C = "C"


class ParamKind(str, Enum):
    """How the tuning UI should render and validate a parameter."""

    MONEY = "money"
    COUNT = "count"
    RATIO = "ratio"
    DAYS = "days"
    MINUTES = "minutes"
    SPEED = "speed"


@dataclass(frozen=True)
class Param:
    """One tunable number on a rule, with the bounds it must stay inside."""

    key: str
    label: str
    kind: ParamKind
    default: float
    minimum: float
    maximum: float
    step: float
    # What moving this costs. Shown under the control, because a threshold with
    # no stated trade-off invites turning sensitivity down until the queue is
    # empty, which is not the same as having no risk.
    hint: str


@dataclass(frozen=True)
class RuleTemplate:
    """A rule the engine knows how to evaluate."""

    key: str
    family: Family
    label: str
    # The typology this detects, in an analyst's words.
    description: str
    # Why the test is built this way — the justification an auditor asks for.
    rationale: str
    params: tuple[Param, ...]
    # Whether an instance may be scoped to a subset of merchant categories.
    scopable: bool = True

    def defaults(self) -> dict[str, float]:
        return {p.key: p.default for p in self.params}


# ---------------------------------------------------------------------------
# Family B — typology rules
# ---------------------------------------------------------------------------

STRUCTURING = RuleTemplate(
    key="structuring_below_threshold",
    family=Family.B,
    label="Structuring below a reporting threshold",
    description=(
        "Several transactions in one day sitting just under a reporting "
        "threshold, when the merchant's normal ticket is nowhere near it."
    ),
    rationale=(
        "Splitting one large payment into several just-under-the-line ones is "
        "the defining smurfing move. No single transaction is unusual, so no "
        "amount baseline can see it; only the clustering just below a known "
        "line is evidence. The band is expressed as a fraction below the "
        "threshold rather than a fixed dollar width so it scales when the "
        "threshold is changed."
    ),
    params=(
        Param("threshold", "Reporting threshold", ParamKind.MONEY, 8000.0,
              1000.0, 200000.0, 500.0,
              "The line transactions are suspected of being kept under. HKD "
              "8,000 is the large-cash-transaction reference point; set it to "
              "whatever line your reporting regime actually uses."),
        Param("band_fraction", "Band width below the threshold", ParamKind.RATIO,
              0.15, 0.01, 0.5, 0.01,
              "How far below the threshold still counts as 'just under'. 0.15 "
              "means HKD 6,800-8,000 for an 8,000 threshold. Widening it "
              "catches sloppier structuring and more ordinary large sales."),
        Param("min_count", "Transactions in the band", ParamKind.COUNT, 3,
              2, 50, 1,
              "How many near-threshold transactions in one day before it is a "
              "pattern rather than a coincidence. Two is common in retail; "
              "three in one day at a merchant that never trades near the line "
              "is not."),
        Param("max_baseline_ratio", "Merchant's normal must be below",
              ParamKind.RATIO, 0.5, 0.05, 1.0, 0.05,
              "Only fires when the merchant's usual ticket is below this "
              "fraction of the threshold. Without it, a jeweller whose every "
              "sale is near the line fires every single day."),
    ),
)

REFUND_ABUSE = RuleTemplate(
    key="refund_ratio_spike",
    family=Family.B,
    label="Refund / credit abuse",
    description=(
        "An unusually high share of the day's value refunded, against a "
        "minimum number of refunds so a one-off return cannot trip it."
    ),
    rationale=(
        "Refunds move value back out of the acquiring system with far less "
        "scrutiny than a payout. A merchant refunding most of what it takes "
        "is either failing commercially or being used as a conduit; both need "
        "a human. The ratio is on value rather than count because one large "
        "refund matters more than several small ones."
    ),
    params=(
        Param("min_refunds", "Minimum refunds in the day", ParamKind.COUNT, 3,
              1, 100, 1,
              "Below this the rule stays silent. A single refund is ordinary "
              "retail and firing on it would bury the queue."),
        Param("ratio", "Refund share of day's value", ParamKind.RATIO, 0.30,
              0.05, 1.0, 0.05,
              "Refunded value divided by gross value taken. Lower catches more "
              "and will pick up genuine seasonal returns."),
    ),
)

BUST_OUT = RuleTemplate(
    key="bust_out",
    family=Family.B,
    label="Bust-out",
    description=(
        "A quiet history, then a sharp climb in daily value, then refunds — "
        "the shape of a merchant maximising takings before disappearing."
    ),
    rationale=(
        "Each phase alone is unremarkable: growth is what a healthy merchant "
        "does, and refunds are ordinary. The sequence is the signal, which is "
        "why no single-day statistic can see it. Measured as recent daily "
        "value against the earlier level over the same window Family A uses, "
        "so the two cannot disagree about what 'normal' was."
    ),
    params=(
        Param("spike_ratio", "Recent value vs earlier level", ParamKind.RATIO,
              3.0, 1.5, 20.0, 0.5,
              "How many times the earlier daily level the recent days must "
              "reach. Lower catches gentler bust-outs and more ordinary growth."),
        Param("recent_days", "Recent window", ParamKind.DAYS, 7, 2, 60, 1,
              "The climb window. Shorter reacts faster and is noisier."),
        Param("refund_ratio", "Refund share in the recent window",
              ParamKind.RATIO, 0.10, 0.0, 1.0, 0.05,
              "Refund share that must accompany the climb. Set to 0 to flag "
              "the climb alone, before any refunds appear — earlier warning, "
              "many more false alerts."),
    ),
)

DORMANT_REACTIVATION = RuleTemplate(
    key="dormant_reactivation",
    family=Family.B,
    label="Dormant reactivation",
    description=(
        "A merchant silent for a long stretch that comes back with immediate "
        "volume rather than easing in."
    ),
    rationale=(
        "A dormant merchant account is an attractive vehicle: it has history, "
        "it is already onboarded, and nobody is watching it. Genuine "
        "reactivation ramps; a takeover starts at full speed on day one. The "
        "test is the silence length paired with the first-day volume, because "
        "either alone is ordinary."
    ),
    params=(
        Param("silence_days", "Days of silence", ParamKind.DAYS, 45, 7, 365, 1,
              "How long the merchant must have been inactive to count as "
              "dormant. Shorter picks up seasonal and holiday closures."),
        Param("return_count", "Transactions on return", ParamKind.COUNT, 10,
              1, 500, 1,
              "Transactions on the first day back before the return counts as "
              "abrupt. A merchant reopening with two sales is just reopening."),
    ),
)

RAPID_MOVEMENT = RuleTemplate(
    key="rapid_movement",
    family=Family.B,
    label="Rapid movement / pass-through",
    description=(
        "Value arriving and leaving the same day at near-parity, leaving no "
        "resting balance behind."
    ),
    rationale=(
        "Laundering wants the money to keep moving; a real business leaves a "
        "float. We cannot see settlement accounts from here, so this is read "
        "from the transaction record: takings almost exactly matched by "
        "same-day refunds or reversals. Weaker evidence than a settlement "
        "view, and labelled as such rather than overclaimed."
    ),
    params=(
        Param("min_value", "Minimum day value", ParamKind.MONEY, 20000.0,
              0.0, 1000000.0, 1000.0,
              "Below this the day is too small to be worth calling a "
              "pass-through, however balanced it looks."),
        Param("match_tolerance", "In/out match tolerance", ParamKind.RATIO,
              0.10, 0.01, 0.5, 0.01,
              "How closely value out must match value in. 0.10 means within "
              "10%. Widening it catches sloppier pass-through and ordinary "
              "high-return days."),
    ),
)

DECLARED_MISMATCH = RuleTemplate(
    key="declared_vs_actual_mismatch",
    family=Family.B,
    label="Declared vs actual mismatch",
    description=(
        "The merchant's registered category does not match how it actually "
        "trades — the transaction-laundering signature."
    ),
    rationale=(
        "A registered business fronting a hidden one is the case where every "
        "individual transaction is legitimate-looking and the whole "
        "relationship is a lie. Detected as sustained divergence from the "
        "merchant's own declared cohort on ticket size and operating hours "
        "together: one alone is a merchant with a niche; both, persistently, "
        "is a merchant in the wrong category."
    ),
    params=(
        Param("ticket_ratio", "Ticket vs category median", ParamKind.RATIO,
              4.0, 1.5, 50.0, 0.5,
              "How many times its category's typical ticket the merchant's own "
              "must be. Set high: being expensive for your trade is legal."),
        Param("hours_overlap", "Minimum overlap with category hours",
              ParamKind.RATIO, 0.25, 0.0, 1.0, 0.05,
              "Share of the merchant's transactions that must fall inside its "
              "category's normal operating hours. Below this the trading "
              "pattern does not look like the declared trade."),
    ),
)

DECLINE_SPIKE = RuleTemplate(
    key="decline_ratio_spike",
    family=Family.B,
    label="Decline-ratio spike",
    description=(
        "An unusually high share of failed authorisations — the merchant-side "
        "read of card testing."
    ),
    rationale=(
        "We measure the merchant's decline rate; we do not chase stolen cards. "
        "A merchant whose terminal is being used to test stolen card numbers "
        "shows a decline rate no ordinary retailer produces, and that is a "
        "merchant-integrity question squarely in scope."
    ),
    params=(
        Param("min_attempts", "Minimum authorisations", ParamKind.COUNT, 20,
              5, 1000, 1,
              "Below this the ratio is noise — three declines out of four "
              "attempts is a bad afternoon, not a pattern."),
        Param("ratio", "Declined share of attempts", ParamKind.RATIO, 0.25,
              0.05, 1.0, 0.05,
              "Declines divided by all authorisation attempts. Ordinary retail "
              "sits in low single digits."),
    ),
)


# ---------------------------------------------------------------------------
# Family C — ring / cross-merchant rules
# ---------------------------------------------------------------------------

IDENTITY_RING = RuleTemplate(
    key="shared_identity_ring",
    family=Family.C,
    label="Shared merchant identity",
    description=(
        "Several distinct merchant IDs sharing a business registration "
        "number, address, or trading name."
    ),
    rationale=(
        "The classic shell-merchant ring: several 'independent' storefronts "
        "behind one owner, spreading synthetic sales under each single-merchant "
        "radar. Detection is an equality join on the hashes we already hold — "
        "we never reverse anything, so the hashes' reversibility is irrelevant "
        "here, which is why this layer is cheap and in scope while the card "
        "layer is gated."
    ),
    scopable=False,
    params=(
        Param("min_members", "Merchants sharing an identity", ParamKind.COUNT,
              3, 2, 100, 1,
              "Group size before it is a ring. Two merchants sharing an "
              "address is a landlord; several is a structure."),
        Param("min_flagged", "Members already flagged", ParamKind.COUNT, 1,
              0, 50, 1,
              "How many of the group must already carry an alert. Set to 0 to "
              "surface every shared-identity group regardless of behaviour — "
              "useful for onboarding review, noisy for a daily queue."),
    ),
)

AGENT_CONCENTRATION = RuleTemplate(
    key="agent_alert_concentration",
    family=Family.C,
    label="Onboarding agent concentration",
    description=(
        "One onboarding agent whose book of merchants alerts far more often "
        "than the portfolio as a whole."
    ),
    rationale=(
        "A gatekeeper-of-the-gatekeeper signal that no per-merchant view can "
        "produce. If one agent's entire book runs hot, the problem is upstream "
        "of any individual merchant. Compared against the portfolio rate "
        "rather than an absolute number so it does not fire on a large agent "
        "simply for being large."
    ),
    scopable=False,
    params=(
        Param("min_merchants", "Minimum merchants in the book", ParamKind.COUNT,
              5, 2, 500, 1,
              "Below this the agent's rate is not a rate. One bad merchant out "
              "of two is 50% and means nothing."),
        Param("rate_multiple", "Alert rate vs portfolio", ParamKind.RATIO, 3.0,
              1.2, 20.0, 0.1,
              "How many times the portfolio-wide alert rate the agent's book "
              "must reach."),
    ),
)

BRANCH_STRUCTURING = RuleTemplate(
    key="card_across_related_merchants",
    family=Family.C,
    label="One card across related merchants",
    description=(
        "A single card used at several merchant IDs belonging to the same "
        "owner within one day — structuring across branches."
    ),
    rationale=(
        "The source schema carries no terminal identifier, so 'different "
        "branches of the same merchant' is expressed as distinct merchant IDs "
        "sharing an identity hash. A hard daily count, not a statistic: a "
        "customer visiting three branches of a chain in a day is plausible, "
        "and a fourth is the point at which an explanation is owed. A count an "
        "officer can defend in a report beats a z-score nobody can explain."
    ),
    scopable=False,
    params=(
        Param("max_branches", "Branches allowed per card per day",
              ParamKind.COUNT, 3, 1, 20, 1,
              "Visiting up to this many related merchants in one day is "
              "allowed. Exceeding it raises an alert."),
    ),
)

CARD_SWARM = RuleTemplate(
    key="card_swarm",
    family=Family.C,
    label="Card swarming across unrelated merchants",
    description=(
        "One card appearing at many *unrelated* merchants inside a short "
        "window — a ring moving through a district."
    ),
    rationale=(
        "Distinct from branch structuring: there is no ownership link between "
        "the merchants, so the coordination is on the card side. Kept on a "
        "short window because a card touching many merchants over a month is "
        "just a person living their life."
    ),
    scopable=False,
    params=(
        Param("min_merchants", "Distinct merchants", ParamKind.COUNT, 5,
              2, 100, 1,
              "How many unrelated merchants one card must touch inside the "
              "window."),
        Param("window_minutes", "Window", ParamKind.MINUTES, 120, 5, 1440, 5,
              "The span the merchants must fall inside. Widening it turns "
              "ordinary shopping into a swarm."),
    ),
)

GEO_VELOCITY = RuleTemplate(
    key="impossible_geo_velocity",
    family=Family.C,
    label="Impossible geo-velocity",
    description=(
        "One card used at two places further apart than the time between the "
        "transactions allows."
    ),
    rationale=(
        "Distance between the two subdistrict centroids divided by the elapsed "
        "time. Distances come from a committed coordinate table, never a maps "
        "API, so the result is reproducible and works air-gapped. Centroids "
        "understate the real journey, so the implied speed is a lower bound "
        "and the rule under-flags by construction.\n\n"
        "Scope note: the detection-layer spec originally placed this out of "
        "scope as cardholder fraud rather than merchant integrity. It is built "
        "here as a deliberate scope change — a card physically impossible to "
        "have been present at both merchants means at least one of them "
        "accepted a card that was not there, which is a merchant-acceptance "
        "question. Its output is a ring signal, not a fraud verdict."
    ),
    scopable=False,
    params=(
        Param("max_kmh", "Maximum plausible speed", ParamKind.SPEED, 60.0,
              5.0, 300.0, 5.0,
              "Above this the journey is treated as impossible. Hong Kong "
              "door-to-door speed is bounded by MTR and road traffic, so 60 "
              "km/h is already generous; walking pace would flag every card "
              "used in two districts on the same day."),
        Param("min_km", "Minimum distance", ParamKind.COUNT, 3.0, 0.0, 50.0, 0.5,
              "Journeys shorter than this are ignored. Centroid distance is "
              "meaningless at short range — two merchants either side of a "
              "subdistrict boundary read as far apart as their centroids."),
        Param("max_minutes", "Maximum gap considered", ParamKind.MINUTES, 240,
              5, 1440, 5,
              "Pairs further apart in time than this are not compared. Over a "
              "long enough gap every journey is possible, so testing them only "
              "costs time."),
    ),
)


TEMPLATES: tuple[RuleTemplate, ...] = (
    STRUCTURING,
    REFUND_ABUSE,
    BUST_OUT,
    DORMANT_REACTIVATION,
    RAPID_MOVEMENT,
    DECLARED_MISMATCH,
    DECLINE_SPIKE,
    IDENTITY_RING,
    AGENT_CONCENTRATION,
    BRANCH_STRUCTURING,
    CARD_SWARM,
    GEO_VELOCITY,
)

BY_KEY: dict[str, RuleTemplate] = {t.key: t for t in TEMPLATES}


@dataclass(frozen=True)
class RuleInstance:
    """One configured rule: a template plus the parameters actually in force.

    `instance_id` distinguishes several instances of the same template, so a
    compliance officer can run structuring at HKD 8,000 portfolio-wide and a
    second, stricter instance at HKD 50,000 for jewellers.
    """

    instance_id: str
    template: str
    enabled: bool = True
    params: dict[str, float] = field(default_factory=dict)
    # Restrict to these MCCs. Empty means every merchant.
    mcc_scope: tuple[str, ...] = ()
    # Set on instances a person added, so the UI can offer to delete them and
    # the shipped set stays intact.
    custom: bool = False
    label: str | None = None

    def spec(self) -> RuleTemplate:
        return BY_KEY[self.template]

    def value(self, key: str) -> float:
        """A parameter's effective value, falling back to the template default.

        Falling back rather than raising means adding a parameter to a template
        does not invalidate every instance already stored.
        """
        if key in self.params:
            return self.params[key]
        for p in self.spec().params:
            if p.key == key:
                return p.default
        raise KeyError(f"{self.template} has no parameter {key!r}")

    def applies_to(self, mcc: str | None) -> bool:
        return not self.mcc_scope or (mcc or "") in self.mcc_scope

    def display_label(self) -> str:
        return self.label or self.spec().label

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "template": self.template,
            "enabled": self.enabled,
            "params": dict(self.params),
            "mcc_scope": list(self.mcc_scope),
            "custom": self.custom,
            "label": self.label,
        }

    @staticmethod
    def from_dict(raw: dict) -> "RuleInstance":
        template = str(raw.get("template") or "")
        if template not in BY_KEY:
            raise ValueError(f"unknown rule template: {template!r}")
        known = {p.key for p in BY_KEY[template].params}
        return RuleInstance(
            instance_id=str(raw.get("instance_id") or template),
            template=template,
            enabled=bool(raw.get("enabled", True)),
            # Drop parameters the template no longer declares, so a stored
            # instance cannot resurrect a retired knob.
            params={
                k: float(v) for k, v in (raw.get("params") or {}).items() if k in known
            },
            mcc_scope=tuple(str(m) for m in (raw.get("mcc_scope") or [])),
            custom=bool(raw.get("custom", False)),
            label=raw.get("label") or None,
        )


def default_instances(family: Family | None = None) -> list[RuleInstance]:
    """The shipped rule set: one instance per template, at its defaults."""
    return [
        RuleInstance(instance_id=t.key, template=t.key)
        for t in TEMPLATES
        if family is None or t.family is family
    ]


def template_as_dict(t: RuleTemplate) -> dict:
    return {
        "key": t.key,
        "family": t.family.value,
        "label": t.label,
        "description": t.description,
        "rationale": t.rationale,
        "scopable": t.scopable,
        "params": [
            {**asdict(p), "kind": p.kind.value} for p in t.params
        ],
    }
