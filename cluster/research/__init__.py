"""Side-effect-free research lock validation helpers."""

from .locks import (
    PINNED_RPC_COMMIT,
    LockValidationError,
    assess_formal_eligibility,
    canonical_json_bytes,
    lock_set_sha256,
    validate_condition_lock,
    validate_model_lock,
    validate_prompt_lock,
    validate_runtime_lock,
)
from .campaign import (
    CampaignRepository,
    CampaignRunner,
    CampaignStateError,
    CampaignValidationError,
    build_campaign_manifest,
    validate_campaign_manifest,
)
from .eligibility import DRIFT_CODES, assess_campaign_cell
from .scheduler import (
    ORDER_POLICIES,
    coverage_from_cells,
    schedule_condition_order,
    seeded_randomized_block_order,
)
from .statistical_campaign import (
    StatisticalCampaignError,
    aggregate_campaign_attempts,
    aggregate_run_summaries,
    describe_samples,
)

__all__ = [
    "PINNED_RPC_COMMIT",
    "LockValidationError",
    "assess_formal_eligibility",
    "canonical_json_bytes",
    "lock_set_sha256",
    "validate_condition_lock",
    "validate_model_lock",
    "validate_prompt_lock",
    "validate_runtime_lock",
    "CampaignRepository",
    "CampaignRunner",
    "CampaignStateError",
    "CampaignValidationError",
    "build_campaign_manifest",
    "validate_campaign_manifest",
    "DRIFT_CODES",
    "assess_campaign_cell",
    "coverage_from_cells",
    "ORDER_POLICIES",
    "schedule_condition_order",
    "seeded_randomized_block_order",
    "StatisticalCampaignError",
    "aggregate_campaign_attempts",
    "aggregate_run_summaries",
    "describe_samples",
]
