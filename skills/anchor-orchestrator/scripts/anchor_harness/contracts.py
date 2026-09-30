"""Planning values; convert cohorts explicitly at a runtime boundary."""
from dataclasses import dataclass
from enum import StrEnum
from .canonical import canonical_json, canonical_digest
from .validation_values import require_id, require_unique

class NodeKind(StrEnum):
    PURE = "pure"
    EVIDENCE_READ = "evidence_read"
    LOCAL_DURABLE_WRITE = "local_durable_write"
    EXTERNAL_READ = "external_read"
    EXTERNAL_EFFECT = "external_effect"
    HUMAN_GATE = "human_gate"
    VERIFICATION = "verification"


@dataclass(frozen=True, slots=True)
class CommitCohort:
    cohort_id: str
    source_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    effect_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.cohort_id, "cohort_id")
        # V2-RATCHET-010 requires every proposed position, effect, proof, and
        # candidate to belong to a cohort -- it does not require every cohort to
        # own a source. A publish-only cohort that advances no source position is
        # legitimate, so the rule is "at least one member of any kind". Demanding
        # a source here also made Ratchet's own emptiness check unreachable.
        if not (self.source_ids or self.output_ids or self.effect_ids):
            raise ValueError("a commit cohort requires at least one source, output, or effect")
        require_unique(self.source_ids, "source_ids")
        require_unique(self.output_ids, "output_ids")
        require_unique(self.effect_ids, "effect_ids")
