# S10 — 전체 회귀·장애 주입·사용자 수동 실험 인수

작성일: 2026-09-19

단계: S10 only

기준 branch: `codex/current-source-pilot-v6`

기준 commit/upstream: `239a8d7e7f8dd966dbee4c78614b08e3c45d1699`

## 범위와 판정

S00–S09의 구현을 현재 source에서 통합 검증했다. 실제 inventory, 연구용 lock,
기존 run/result, product model 파일은 수정하지 않았다. 실제 Worker 접속, SSH, 모델 다운로드,
inference, native RPC, 전력 변경, hardware workflow dispatch는 실행하지 않았다. 새 통합 검사는
임시 `127.0.0.1` HTTP server, injected fake native runtime, fake durable backend와
`TemporaryDirectory`만 사용한다.

- **Software implementation:** COMPLETE for the specified S00–S10 software scope.
  Preview에서 만든 model/context/RPC condition이 concrete `ExperimentConfig`, HTTP Worker
  request와 native RPC argv까지 연결된다.
- **Automated tests:** PASS for all runnable repository tests and S10 integration tests. 로컬에서
  Python 679개와 npm 전체 gate 4 browser tests가 통과했다.
- **Git/CI delivery:** 이 문서를 포함한 S10 checkpoint를 feature branch에 non-force push한다.
  실제 commit/push와 원격 CI 결과는 commit 생성 뒤 사용자에게 별도로 보고한다.
- **Hardware acceptance:** NOT RUN. 실제 모델 설치, 장비 inference/RPC, 성능·전력·메모리
  성공 여부는 사용자의 수동 hardware acceptance 범위다.

## 필수 통합 시나리오

`cluster.tests.test_s10_integration`의 6개 검사가 다음 경계를 연결한다.

1. `test_108_preview_budget_and_concrete_runner_use_loopback_worker`: 36 unique cells/108 trials와
   workload budget을 확인하고 서로 다른 모델·context·output 조건 두 개를 실제
   `BenchmarkRunner → _load_model → urllib → 127.0.0.1 fake Worker` 경로로 실행했다.
2. `test_36_rpc_trials_bind_profile_arguments_and_cleanup`: 12 unique cells/36 trials 전부를
   `build_cell_config → WorkerRpcBackend → FakeNative`로 적용했다. 모델, 1024/2048 context,
   layer/row, auto/equal/custom, Worker 순서, `-`/`1,1`/`3,2,1` split, all/17 GPU layers와
   cell별 cleanup 완료를 확인했다.
3. `test_disjoint_a_b_overlap_c_waits_and_targeted_cancel_keeps_b`: j1 fake Jetson child A와
   j2 fake Pi child B를 cap 2로 함께 실행하고, j1과 겹치는 C를 pending으로 유지했다.
   A backend job만 취소한 후 B의 attempt/job identity는 그대로였고 C가 빈 slot에 진입했다.
4. `test_restart_response_loss_crash_claim_result_recovery_and_cleanup_timeout`와
   `test_partial_rpc_start_cleans_all_attempted_devices`: active 2개 상태의 supervisor 재생성,
   Start 응답 유실, result가 manifest보다 먼저 terminal이 된 창, crash-after-claim,
   cleanup quarantine과 partial RPC start를 주입했다. 기존 attempt/job ID를 재사용했고
   cleanup 불확실성은 pause/reconciliation으로 닫혔다.
5. `test_old_worker_parallel_is_blocked_and_default_single_job_still_starts`: ownership protocol이
   없는 Worker의 cap 2 시작은 child 생성 전에 거절하고 기존 cap 1 경로는 유지했다.

## A01–A65 증거 매핑

아래 `PASS`는 표에 적은 자동 검사 또는 repository/Git 증거의 결과다. `HW NOT RUN`은
software contract와 fake 경로는 통과했지만 실제 장비 결과를 만들지 않았다는 뜻이다.
모든 Python 항목은 이번 S10의 667-test 일반 회귀, 6-test launcher 또는 6-test S10 명령에
포함돼 실제 실행됐다.

| ID | 실행 test/증거 | 결과 |
|---|---|---|
| A01 | `S00-report.md`; S00-only checkpoint `e486cd7`, 재점검 `678c046` | PASS |
| A02 | `test_sweep_planner.py::test_unknown_empty_axes_duplicates_and_template_are_errors`; `test_sweep_api.py::test_valid_preview_save_start_list_get_export_and_auth_policy` | PASS |
| A03 | `test_s10_integration.py::test_108_preview_budget_and_concrete_runner_use_loopback_worker`; `test_sweep_planner.py::test_grid_108_trials_and_independent_repeat_ids` | PASS |
| A04 | `test_sweep_planner.py::test_huge_grid_rejected_before_product_or_cell_materialization`; `test_sweep_api.py::test_unknown_fields_invalid_axes_huge_grid_and_malicious_ids` | PASS |
| A05 | `test_sweep_planner.py::test_one_at_a_time_baseline_once_with_duplicate_rows_retained` | PASS |
| A06 | `test_sweep_planner.py::test_equivalent_rpc_ratios_dedupe_but_preserve_original_input`; `test_sweep_rpc.py::test_grid_binds_profiles_models_contexts_and_gpu_argv` | PASS |
| A07 | `test_sweep_planner.py::test_missing_model_worker_prompt_and_unknown_are_not_removed`, `test_exclusions_change_revision_hash_and_keep_reason`; browser `Sweep Builder previews 108 trials…` | PASS |
| A08 | `test_sweep_planner.py::test_existing_strategy_planner_agreement_without_nested_double_count`; `test_benchmark_core.py::test_three_broadcast_logical_requests_make_six_physical_calls` | PASS |
| A09 | S10 loopback 108 test; S10 36 RPC test; `test_context_runtime.py::test_api_config_runner_worker_factory_two_values` | PASS |
| A10 | `test_context_runtime.py::test_prepared_tokens_reused_and_early_eos_distinct_from_limit`, `test_rpc_finish_reason_and_output_limit_remain_distinct` | PASS |
| A11 | `test_context_runtime.py::test_api_config_runner_worker_factory_two_values` | PASS |
| A12 | `test_context_runtime.py::test_cache_identity_covers_every_load_parameter`, `test_same_path_binary_replacement_reloads_even_with_mtime_restored` | PASS |
| A13 | `test_context_runtime.py::test_exact_preparation_special_tokens_budget_and_private_storage`; `test_sweep_planner.py::test_context_overflow_including_output_reserve_and_token_target` | PASS |
| A14 | `test_context_runtime.py::test_unknown_effective_values_never_copied_from_request`, `test_preparation_unsupported_has_safe_reason_and_no_generation` | PASS |
| A15 | `test_context_runtime.py::test_sweep_context_gpu_retry_not_accepted_even_uniform_false`, `test_default_batch_fallback_is_a_sweep_mismatch`; `test_sweep_results.py::test_repeats_retry_adjustment_early_eos_energy_overlap_and_ci` | PASS |
| A16 | `test_sweep_models.py::test_two_models_pin_identity_and_keep_model_specific_prompt_evidence`, `test_two_bound_models_use_own_fake_template_and_tokens` | PASS |
| A17 | `test_sweep_models.py::test_same_filename_different_sha_and_identity_fields_do_not_merge` | PASS |
| A18 | `test_sweep_models.py::test_license_gated_access_and_formal_approval_are_separate` | PASS |
| A19 | `test_sweep_models.py::test_missing_and_catalog_only_candidates_remain_visible`; `test_sweep_runner.py::test_drift_blocks_before_claim_and_does_not_download_or_relock` | PASS |
| A20 | `test_sweep_models.py::test_broadcast_and_node_sweep_keep_one_model_per_cell`, `test_suite_cannot_reexpand_bound_sweep_child` | PASS |
| A21 | `test_sweep_models.py::test_memory_and_context_are_per_condition_not_parameter_count`; manual guide states no size guarantee | PASS / HW NOT RUN |
| A22 | S10 36 RPC test; `test_sweep_rpc.py::test_grid_binds_profiles_models_contexts_and_gpu_argv` | PASS |
| A23 | `test_rpc_coordinator.py::test_topology_and_command_use_actual_worker_coordinator`; S10 36 RPC test creates a new session per trial | PASS |
| A24 | S10 36 RPC test; `test_sweep_rpc.py::test_auto_policy_is_not_rewritten_to_equal` | PASS |
| A25 | `test_sweep_rpc.py::test_unreported_pinned_capability_blocks_before_start`; `test_sweep_planner.py::test_rpc_integer_gpu_and_row_uncertainty_not_rewritten` | PASS / HW NOT RUN |
| A26 | `test_s10_integration.py::test_partial_rpc_start_cleans_all_attempted_devices`; `test_sweep_rpc.py::test_checksum_template_and_context_mismatch_cleanup_attempted_devices` | PASS |
| A27 | `test_sweep_rpc.py::test_residual_port_blocks_next_cell_before_start`; `test_resource_coordinator.py::test_cleanup_failure_and_heartbeat_loss_quarantine_without_ttl_release` | PASS |
| A28 | S10 pool A/B/C test; `test_resource_coordinator.py::test_disjoint_worker_sets_both_fit_opt_in_cap` | PASS |
| A29 | `test_resource_coordinator.py::test_overlapping_worker_sets_are_atomic_across_processes`; S10 pool A/B/C test | PASS |
| A30 | `test_resource_coordinator.py::test_aliases_with_same_physical_endpoint_are_rejected` | PASS |
| A31 | `test_resource_coordinator.py::test_targeted_cancel_and_pause_leave_disjoint_job_running` | PASS |
| A32 | S10 pool A/B/C test; resource coordinator targeted cancel test | PASS |
| A33 | `test_resource_coordinator.py::test_stale_owner_cannot_mutate_or_release_new_owner`, `test_cooldown_holds_resource_and_old_owner_cannot_release_new_owner` | PASS |
| A34 | `test_resource_coordinator.py::test_stale_heartbeat_is_quarantined_and_registry_corruption_fails_closed`, `test_crash_after_spawn_quarantines_instead_of_freeing_by_pid_or_ttl` | PASS |
| A35 | `test_resource_coordinator.py::test_formal_and_ordinary_start_race_has_one_winner` | PASS |
| A36 | `test_research_campaign.py::test_drift_pauses_without_start_and_pi_history_warning_does_not`; ordinary S10 loopback execution | PASS |
| A37 | `test_resource_coordinator.py::test_model_deletion_action_conflicts_with_running_job` | PASS |
| A38 | `test_resource_coordinator.py::test_cli_mutation_observes_dashboard_reservation_without_remote_access`; Dashboard/JobService resource tests | PASS |
| A39 | S10 old Worker/default test; `test_sweep_runner.py::test_disjoint_parallel_overlap_and_continue_ready` | PASS |
| A40 | S10 restart/response-loss test; `test_resource_coordinator.py::test_duplicate_job_start_spawns_one_child`; `test_sweep_api.py::test_duplicate_start_race_uses_one_durable_child` | PASS |
| A41 | S10 restart/claim test; `test_sweep_runner.py::test_crash_after_claim_reuses_same_attempt_and_job_id` | PASS |
| A42 | S10 result-before-manifest reconciliation; `test_sweep_runner.py::test_claim_response_loss_restart_and_completed_trial_never_relaunch` | PASS |
| A43 | `test_sweep_runner.py::test_pause_at_safe_boundary_cancel_scope_and_manual_retry_history`; API lifecycle test | PASS |
| A44 | sweep runner pause/retry test; `test_sweep_results.py::test_repeats_retry_adjustment_early_eos_energy_overlap_and_ci` | PASS |
| A45 | S10 pool A/B/C test; `test_sweep_runner.py::test_disjoint_parallel_overlap_and_continue_ready` | PASS |
| A46 | `test_sweep_runner.py::test_rpc_context_model_concurrency_compound_plan_uses_one_model_children`; S10 pool cap=2 evidence | PASS |
| A47 | `test_power_policy.py::test_history_only_is_warning`, `test_preflight_warning_is_additive_and_nonblocking`; `test_dashboard_research.py::test_legacy_result_is_visible_with_explicit_unknown_fallbacks` | PASS / HW NOT RUN |
| A48 | `test_research_campaign.py::test_pi_history_is_warning_but_missing_or_active_evidence_blocks`; `test_research_locks.py::test_jetson_power_mismatch_blocks_formal_eligibility` | PASS |
| A49 | `test_sweep_api.py::test_explicit_refresh_is_the_only_resolver_path_that_refreshes_status`, `test_preview_save_start_duplicate_and_server_fresh_preflight` | PASS |
| A50 | `test_sweep_planner.py::test_plan_tamper_detects_hash_ids_counts_config_and_approval`; API tamper/auth tests | PASS |
| A51 | `test_sweep_api.py::test_http_lifecycle_parallel_overlap_results_and_sse_reconnect`; `test_results_failures.py::test_completed_request_is_durable_before_final_summary` | PASS |
| A52 | Playwright `Dashboard core flow…`; `Sweep Builder previews 108 trials and controls disjoint durable runs without hidden authority` | PASS, 4 browser tests |
| A53 | `test_sweep_results.py::test_repeats_retry_adjustment_early_eos_energy_overlap_and_ci`; browser sweep result assertions | PASS |
| A54 | `test_sweep_results.py::test_tokenizer_rpc_node_sweep_and_historical_separation_contracts` | PASS |
| A55 | same tokenizer/RPC/node-sweep separation test; Dashboard fixture/browser gate | PASS |
| A56 | sweep results repeats/CI test | PASS |
| A57 | sweep results repeats/energy test; `test_measurement_instrumentation.py::test_energy_is_null_when_sensor_or_second_sample_is_missing` | PASS |
| A58 | `test_context_runtime.py::test_persistence_privacy_and_finish_reason_without_csv_change`; sweep results export privacy test; API privacy scrub test | PASS |
| A59 | `test_sweep_results.py::test_export_index_privacy_formula_protection_and_fixed_header` | PASS |
| A60 | `test_results_failures.py::test_legacy_result_without_response_journal_remains_readable`; `test_measurement_instrumentation.py::test_measurement_journal_is_private_and_csv_stays_exactly_19_columns` | PASS |
| A61 | S10 36 RPC test | PASS |
| A62 | 실행 명령·fixture audit, loopback only, hardware workflow 미dispatch | PASS software safety / HW NOT RUN |
| A63 | S00–S09 phase commits와 reports; S10 report 저장 후 feature branch commit/non-force push | commit/push 후 최종 보고 |
| A64 | S10 old Worker parallel/default test | PASS |
| A65 | `test_research_locks.py::test_shipped_locks_validate_and_share_fingerprint`; Git diff에서 formal lock/inventory 무변경 | PASS |

## 실제 quality gate 결과

| 명령 | 결과 |
|---|---|
| S10 module: `.venv/bin/python -m unittest cluster.tests.test_s10_integration` | 6 tests, OK, 0.995s; localhost bind 권한만 좁게 허용 |
| launcher 제외 Python 전체 | 667 tests, OK, 30.595s |
| `.venv/bin/python -m unittest cluster.tests.test_launcher` | 6 tests, OK, 1.529s; localhost 임시 Dashboard lifecycle |
| Python 합계 | 679 tests, PASS |
| `PYTHONPYCACHEPREFIX=/private/tmp/s10-sweep-handoff/pycache .venv/bin/python -m compileall -q cluster scripts/ci` | exit 0 |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| repository의 모든 `.sh`에 `bash -n` | exit 0 |
| `.venv/bin/python -m unittest cluster.tests.test_packaging -v` | 3 tests, PASS; 임시 복사에서 offline wheel build/install/import |
| `.venv/bin/python -m build --wheel --no-isolation` | NOT RUN: local venv/base Python에 CI가 설치하는 `build` frontend 없음 (`No module named build.__main__`) |
| ShellCheck | NOT RUN: executable 미설치 |
| `npm test` | PASS; syntax, fixtures, publication PNG, Playwright 4 tests (25.5s) |

Sandbox에서 localhost bind와 Chromium Mach port는 거절됐으므로 S10/launcher 모듈과 `npm test`
각각만 좁게 권한을 허용해 다시 실행했다. 광범위한 Python suite에는 권한을 확대하지 않았다.
테스트 출력의 SSH/RPC/setup 문자열은 기존 mock fixture다. 실제 endpoint 접근은 없었다.

## R package overlap과 남은 debt

| Package | 현재 feature와의 overlap | S10 이후 상태 |
|---|---|---|
| R01 source gate | 기존 formal source/identity gate를 그대로 회귀 | 충족; lock 재승인·완화 없음 |
| R02 energy coverage | scenario 간 gap 분리와 S09 unknown/partial 표시 | **후속 완료:** schema v4 `bounded-power-gap-v1`이 내부 결측/긴 gap을 fail closed 처리하고 coverage를 UI/export에 전달함. [R02 report](R02-report.md) |
| R03 Pi policy | ordinary warning 비차단, formal active/missing evidence gate | software 회귀 PASS; 실제 Pi acceptance 미실행 |
| R04 Campaign control | JobService/state-machine/resource primitives 공유 | **잔여:** formal Campaign용 live Dashboard start/control adapter는 이번 exploratory sweep 범위 밖 |
| R05 multipart | 해당 model만 명시적으로 blocked | **잔여:** logical multipart artifact-set 설치/loader 미구현 |
| R06 compatibility | 설치/checksum/license, runtime verified, formal approval을 분리 | **잔여:** 실제 architecture/runtime/model 조합은 hardware evidence 필요 |
| R07/H01 | S10 fake integration과 수동 인수 문서가 경계를 제공 | hardware 단계는 자동 시작하지 않음 |

사용자 가이드는 `docs/manual/sweep-context-model-rpc.md`, 실제 API 필드 기반 비실행 예제는
`docs/manual/examples/*.template.json`에 저장했다. 실제 model hash/endpoint 증거가 없으므로
예제는 unresolved template 상태다. S10 이후 새로운 software phase를 시작하지 않는다.
