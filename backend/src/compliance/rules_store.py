"""Persistence for the Family B and Family C rule sets.

Separate from `settings_store` on purpose. Family A's settings are a handful
of global numbers; a rule set is a *list* of configured instances that a
compliance officer adds to and removes from. Sharing one blob would mean every
threshold change rewrites the rules and vice versa, and the tuning screen could
not present them as the different kinds of thing they are.

Stored in the same `detection_settings` table under distinct keys, so both
inherit its audit columns (`updated_at`, `updated_by`) for free.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.ruleset import (
    BY_KEY,
    Family,
    RuleInstance,
    default_instances,
)
from compliance.models import DetectionSetting

RULES_KEY = "rules"


def load_rules(session: Session, family: Family | None = None) -> list[RuleInstance]:
    """The rule set in force, or the shipped defaults if none was ever saved.

    A stored instance naming a template that no longer exists is dropped
    rather than raising: a retired rule must not stop the nightly run, and the
    alternative is a pipeline that cannot start until someone edits a JSON blob
    in the database.
    """
    row = session.scalars(
        select(DetectionSetting).where(DetectionSetting.key == RULES_KEY)
    ).first()

    if row is None or not (row.value or {}).get("instances"):
        instances = default_instances()
    else:
        instances = []
        for raw in row.value["instances"]:
            try:
                instances.append(RuleInstance.from_dict(raw))
            except (ValueError, TypeError):
                continue

    if family is None:
        return instances
    return [i for i in instances if BY_KEY[i.template].family is family]


def active_rules(session: Session, family: Family | None = None) -> list[RuleInstance]:
    """Only the enabled instances — what the pipeline should actually run."""
    return [i for i in load_rules(session, family) if i.enabled]


def save_rules(session: Session, instances: list[RuleInstance]) -> None:
    """Replace the whole rule set.

    Whole-set replacement rather than per-instance patching because the tuning
    screen edits a draft and saves it: a partial update would let two officers
    editing at once silently merge into a set neither of them chose.
    """
    payload = {"instances": [i.as_dict() for i in instances]}
    row = session.scalars(
        select(DetectionSetting).where(DetectionSetting.key == RULES_KEY)
    ).first()
    if row is None:
        session.add(DetectionSetting(key=RULES_KEY, value=payload))
    else:
        row.value = payload


def validate(instances: list[RuleInstance]) -> list[str]:
    """Problems that should block a save, as human-readable messages.

    Parameters are clamped to the template's declared bounds rather than
    accepted as given: a structuring rule with a negative count or a
    geo-velocity rule at 0 km/h would flag the entire portfolio, and a tuning
    screen must not be able to do that by typo.
    """
    problems: list[str] = []
    seen: set[str] = set()

    for inst in instances:
        template = BY_KEY.get(inst.template)
        if template is None:
            problems.append(f"unknown rule template: {inst.template}")
            continue
        if inst.instance_id in seen:
            problems.append(f"duplicate rule id: {inst.instance_id}")
        seen.add(inst.instance_id)

        if inst.mcc_scope and not template.scopable:
            problems.append(
                f"{template.label} is portfolio-wide and cannot be scoped to an MCC"
            )

        for p in template.params:
            if p.key not in inst.params:
                continue
            value = inst.params[p.key]
            if not (p.minimum <= value <= p.maximum):
                problems.append(
                    f"{template.label}: {p.label} must be between "
                    f"{p.minimum:g} and {p.maximum:g} (got {value:g})"
                )

    return problems
