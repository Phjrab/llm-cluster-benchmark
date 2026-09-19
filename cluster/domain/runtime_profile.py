"""Pure S02 provenance and load-condition checks; no runtime access."""
from .errors import DomainValidationError
from .sweep import ref, digest, integer, choice

# Only hashes/IDs/counts may enter this public trace; never prompt/token arrays.
def validate_sweep_trace(value):
    required = {"sweep_id", "plan_sha256", "cell_id", "trial_id", "attempt_id",
                "sweep_repeat_index", "model_sha256", "template_sha256", "prompt_sha256", "prompt_mode"}
    if not isinstance(value, dict) or set(value) - (required | {"target_input_tokens"}) or required - set(value):
        raise DomainValidationError("invalid sweep trace fields")
    for key in required:
        if key.endswith("sha256"):
            digest(value[key])
        elif key == "sweep_repeat_index":
            integer(value[key], key, 1, 500)
        elif key == "prompt_mode":
            choice(value[key], ("same_text", "token_length_profile"))
        else:
            ref(value[key])
    target = value.get("target_input_tokens")
    if value["prompt_mode"] == "token_length_profile":
        integer(target, "target_input_tokens", 1, 16384)
    elif target is not None:
        raise DomainValidationError("same_text cannot set target_input_tokens")


def require_applied_profile(current, config):
    """Never accept a requested value as effective evidence from an old Worker."""
    factory = current.get("factory_config") or {}
    effective = current.get("effective_config") or {}
    names = [k for k in ("n_threads", "n_batch") if getattr(config, k) is not None]
    if config.sweep is not None:
        names += ["n_ctx", "n_gpu_layers"]
        if current.get("adjustment_reasons"):
            raise DomainValidationError("SWEEP_CONDITION_MISMATCH: adjusted load")
        if current.get("model_sha256") != config.sweep["model_sha256"]:
            raise DomainValidationError("SWEEP_MODEL_IDENTITY_MISMATCH")
    for name in names:
        requested = getattr(config, name)
        if factory.get(name) != requested:
            raise DomainValidationError("LOAD_PROFILE_MISMATCH: " + name)
        # Actual GPU layer placement is not exposed; record null, verify argv only.
        if name != "n_gpu_layers" and effective.get(name) != requested:
            raise DomainValidationError("LOAD_PROFILE_UNVERIFIED_OR_MISMATCH: " + name)


def require_prepared_input(prepared, config):
    trace = config.sweep
    for key, value in {
        "preparation_id": trace["attempt_id"], "prompt_sha256": trace["prompt_sha256"],
        "template_hash": trace["template_sha256"], "model_sha256": trace["model_sha256"],
        "effective_n_ctx": config.n_ctx, "output_reserve_tokens": config.max_tokens,
        "input_token_source": "prepared_chat_template",
    }.items():
        if prepared.get(key) != value:
            raise DomainValidationError("SWEEP_INPUT_PREPARATION_UNVERIFIED: " + key)
    count = prepared.get("input_tokens")
    if (prepared.get("input_tokens_exact") is not True or type(count) is not int or count < 1
            or count + config.max_tokens > config.n_ctx):
        raise DomainValidationError("SWEEP_INPUT_BUDGET_UNVERIFIED")
    if trace["prompt_mode"] == "token_length_profile" and count != trace["target_input_tokens"]:
        raise DomainValidationError("TOKEN_PROFILE_TARGET_MISMATCH")
