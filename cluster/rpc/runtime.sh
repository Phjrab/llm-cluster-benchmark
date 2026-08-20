#!/usr/bin/env bash
set -euo pipefail
umask 077

INVOCATION_CWD="$(pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run/cluster"
SOURCE_DIR="$RUN_DIR/llama.cpp-src"
BUILD_DIR="$RUN_DIR/llama.cpp-rpc"
BIN_DIR="$BUILD_DIR/bin"
PINNED_COMMIT="f49e9178767d557a522618b16ce8694f9ddac628"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
PROCESS_GUARD_MODULE="cluster.infrastructure.process_guard"

rpc_server_bin="$BIN_DIR/rpc-server"
llama_server_bin="$BIN_DIR/llama-server"

die() { echo "[ERROR] $*" >&2; exit 1; }

if [[ -L "$PROJECT_ROOT/.run" || -L "$RUN_DIR" ]]; then
  die "RPC runtime directory must not be a symlink"
fi
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
cd "$PROJECT_ROOT"

require_lifecycle_tools() {
  [[ -x "$PYTHON_BIN" ]] || die "virtual environment missing: $PROJECT_ROOT/.venv"
  command -v flock >/dev/null 2>&1 || die "flock is required for safe RPC lifecycle management"
}

require_readiness_tools() {
  command -v ss >/dev/null 2>&1 || die "ss is required to verify RPC listener ownership"
}

validate_port() {
  local port=$1
  [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] || die "invalid RPC port: $port"
  (( port <= 65535 )) || die "invalid RPC port: $port"
}

prepare_private_file() {
  local path=$1 label=$2
  if [[ -L "$path" || ( -e "$path" && ! -f "$path" ) ]]; then
    die "$label path must be a regular non-symlink file: $path"
  fi
  : >>"$path"
  chmod 600 "$path"
}

acquire_lifecycle_lock() {
  local lock_file=$1
  prepare_private_file "$lock_file" "RPC lifecycle lock"
  exec 9>>"$lock_file"
  if ! flock -w 10 9; then
    die "another RPC lifecycle operation is still running: $lock_file"
  fi
}

argv_json() {
  "$PYTHON_BIN" -c \
    'import json, sys; print(json.dumps(sys.argv[1:], separators=(",", ":")))' \
    "$@"
}

configure_explicit_guard_for_cwd() {
  local pid_file=$1 identity_file=$2 executable=$3 service_cwd=$4
  shift 4
  local encoded_argv
  encoded_argv="$(argv_json "$@")"
  GUARD_ARGS=(
    --pid-file "$pid_file"
    --identity-file "$identity_file"
    --cwd "$service_cwd"
    --executable "$executable"
    --argv-json "$encoded_argv"
  )
}

configure_explicit_guard() {
  local pid_file=$1 identity_file=$2 executable=$3
  shift 3
  configure_explicit_guard_for_cwd \
    "$pid_file" "$identity_file" "$executable" "$PROJECT_ROOT" "$@"
}

configure_recorded_guard() {
  local pid_file=$1 identity_file=$2 executable=$3
  GUARD_ARGS=(
    --pid-file "$pid_file"
    --identity-file "$identity_file"
    --cwd "$PROJECT_ROOT"
    --executable "$executable"
    --from-record
  )
}

guard_call() {
  (
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" -m "$PROCESS_GUARD_MODULE" "${GUARD_ARGS[@]}" "$@"
  )
}

adopt_and_stop_legacy() {
  local pid_file=$1 identity_file=$2 executable=$3
  shift 3
  local service_cwd guard_status stop_status
  local -a candidate_cwds=("$PROJECT_ROOT")
  if [[ "$INVOCATION_CWD" != "$PROJECT_ROOT" ]]; then
    # The original launcher inherited the caller's cwd. SSH normally invoked
    # it from the remote home, while all new launches use PROJECT_ROOT.
    candidate_cwds+=("$INVOCATION_CWD")
  fi

  for service_cwd in "${candidate_cwds[@]}"; do
    configure_explicit_guard_for_cwd \
      "$pid_file" "$identity_file" "$executable" "$service_cwd" "$@"
    set +e
    guard_call status --adopt >/dev/null
    guard_status=$?
    set -e
    case "$guard_status" in
      0)
        set +e
        guard_call stop >/dev/null
        stop_status=$?
        set -e
        case "$stop_status" in
          0|3) return 0 ;;
          *) return 1 ;;
        esac
        ;;
      3) return 0 ;;
      *) ;;
    esac
  done
  return 1
}

stop_existing_before_start() {
  local pid_file=$1 identity_file=$2 executable=$3
  shift 3
  local guard_status

  if [[ -e "$identity_file" || -L "$identity_file" ]]; then
    configure_recorded_guard "$pid_file" "$identity_file" "$executable"
    set +e
    guard_call stop >/dev/null
    guard_status=$?
    set -e
    case "$guard_status" in
      0|3) ;;
      *) die "existing RPC process metadata is unsafe; no process was signalled" ;;
    esac
  else
    # A legacy PID-only process can be adopted only while the caller still has
    # the complete original start argv. Any mismatch is fail-closed.
    if ! adopt_and_stop_legacy \
      "$pid_file" "$identity_file" "$executable" "$@"; then
      die "legacy RPC PID cannot be matched to the exact command; no process was signalled"
    fi
  fi

  configure_explicit_guard "$pid_file" "$identity_file" "$executable" "$@"
}

rollback_candidate() {
  local candidate_pid=$1
  local stop_status candidate_status
  if guard_call stop >/dev/null; then
    stop_status=0
  else
    stop_status=$?
  fi
  if [[ "$stop_status" -eq 0 ]]; then
    return 0
  fi
  if guard_call terminate-candidate --pid "$candidate_pid" >/dev/null; then
    candidate_status=0
  else
    candidate_status=$?
  fi
  [[ "$candidate_status" -eq 0 || "$candidate_status" -eq 3 ]]
}

RPC_ROLLBACK_ARMED=0
RPC_ROLLBACK_PID=""

rpc_exit_cleanup() {
  local original_status=$1 candidate_pid rollback_status
  trap - EXIT HUP INT TERM
  if [[ "$RPC_ROLLBACK_ARMED" == 1 ]]; then
    RPC_ROLLBACK_ARMED=0
    candidate_pid="$RPC_ROLLBACK_PID"
    if [[ "$candidate_pid" == pending ]]; then
      # Bash defers most traps, but $! closes the narrow interval between the
      # background spawn and assigning RPC_ROLLBACK_PID.
      set +u
      candidate_pid=$!
      set -u
    fi
    if [[ "$candidate_pid" =~ ^[0-9]+$ ]] && (( candidate_pid > 1 )); then
      if rollback_candidate "$candidate_pid"; then
        rollback_status=0
      else
        rollback_status=$?
      fi
      if [[ "$rollback_status" -ne 0 ]]; then
        echo "[ERROR] interrupted RPC start could not verify exact child cleanup" >&2
      fi
    fi
  fi
  exit "$original_status"
}

arm_candidate_rollback() {
  RPC_ROLLBACK_ARMED=1
  RPC_ROLLBACK_PID=pending
  trap 'rpc_exit_cleanup $?' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

disarm_candidate_rollback() {
  RPC_ROLLBACK_ARMED=0
  RPC_ROLLBACK_PID=""
  trap - EXIT HUP INT TERM
}

cleanup_failed_candidate() {
  local label=$1 candidate_pid=$2 rollback_status
  if rollback_candidate "$candidate_pid"; then
    rollback_status=0
  else
    rollback_status=$?
  fi
  disarm_candidate_rollback
  if [[ "$rollback_status" -ne 0 ]]; then
    echo "[ERROR] $label cleanup could not verify the exact process" >&2
  fi
  return "$rollback_status"
}

candidate_is_managed() {
  local candidate_pid=$1 observed_pid guard_status
  if observed_pid="$(guard_call status)"; then
    guard_status=0
  else
    guard_status=$?
  fi
  [[ "$guard_status" -eq 0 && "$observed_pid" == "$candidate_pid" ]]
}

candidate_owns_port() {
  local port=$1 candidate_pid=$2 sockets line
  if ! sockets="$(ss -ltnpH 2>/dev/null)"; then
    return 1
  fi
  while IFS= read -r line; do
    if [[ "$line" == *":$port "* && "$line" == *"pid=$candidate_pid,"* ]]; then
      return 0
    fi
  done <<<"$sockets"
  return 1
}

stop_recorded_service() {
  local pid_file=$1 identity_file=$2 executable=$3 lock_file=$4
  local guard_status
  require_lifecycle_tools
  acquire_lifecycle_lock "$lock_file"

  if [[ ! -e "$identity_file" && ! -L "$identity_file" ]]; then
    if [[ -e "$pid_file" || -L "$pid_file" ]]; then
      die "legacy RPC PID has no exact argv identity; no process was signalled"
    fi
    return 0
  fi
  configure_recorded_guard "$pid_file" "$identity_file" "$executable"
  set +e
  guard_call stop >/dev/null
  guard_status=$?
  set -e
  case "$guard_status" in
    0|3) return 0 ;;
    *) die "RPC process metadata is unsafe; no process was signalled" ;;
  esac
}

platform_kind() {
  if [[ -f /etc/nv_tegra_release ]] || command -v nvpmodel >/dev/null 2>&1; then
    printf 'jetson'
  elif [[ -r /proc/device-tree/model ]] && tr -d '\000' </proc/device-tree/model | grep -qi 'raspberry pi'; then
    printf 'raspberry-pi'
  else
    printf 'unsupported'
  fi
}

check_runtime() {
  [[ -x "$rpc_server_bin" ]] || die "rpc-server missing: run prepare-rpc"
  [[ -x "$llama_server_bin" ]] || die "llama-server missing: run prepare-rpc"
  [[ -d "$SOURCE_DIR/.git" ]] || die "pinned llama.cpp source missing"
  actual="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
  [[ "$actual" == "$PINNED_COMMIT" ]] || die "runtime commit mismatch: $actual"
  "$rpc_server_bin" --help >/dev/null 2>&1
  server_help="$("$llama_server_bin" --help 2>&1)"
  [[ "$server_help" == *"--rpc SERVERS"* ]] || die "llama-server was built without RPC"
  echo "[OK] llama.cpp RPC commit=$actual platform=$(platform_kind)"
}

prepare_runtime() {
  kind="$(platform_kind)"
  [[ "$kind" != unsupported ]] || die "only Jetson and Raspberry Pi are supported"
  command -v git >/dev/null || die "git is required"
  command -v cmake >/dev/null || die "cmake is required"
  if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    mkdir -p "$SOURCE_DIR"
    git -C "$SOURCE_DIR" init
    git -C "$SOURCE_DIR" remote add origin https://github.com/ggml-org/llama.cpp.git
  fi
  git -C "$SOURCE_DIR" fetch --depth 1 origin "$PINNED_COMMIT"
  git -C "$SOURCE_DIR" checkout --detach "$PINNED_COMMIT"
  common=(
    -S "$SOURCE_DIR" -B "$BUILD_DIR"
    -DCMAKE_BUILD_TYPE=Release -DGGML_RPC=ON -DLLAMA_CURL=OFF
    -DGGML_NATIVE=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  )
  if [[ "$kind" == jetson ]]; then
    PATH="/usr/local/cuda/bin:$PATH" CUDACXX="/usr/local/cuda/bin/nvcc" \
      cmake "${common[@]}" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87
    jobs="${RPC_BUILD_JOBS:-6}"
  else
    cmake "${common[@]}" -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS
    jobs="${RPC_BUILD_JOBS:-4}"
  fi
  cmake --build "$BUILD_DIR" --config Release --target rpc-server llama-server -j "$jobs"
  check_runtime
}

start_worker() {
  local port=$1 host=${2:-0.0.0.0}
  local pid_file identity_file log_file lock_file device candidate_pid
  local guard_status observed_pid rollback_status
  local -a command
  validate_port "$port"
  require_lifecycle_tools
  require_readiness_tools
  check_runtime >/dev/null
  pid_file="$RUN_DIR/rpc_worker_${port}.pid"
  identity_file="$RUN_DIR/rpc_worker_${port}.identity.json"
  log_file="$RUN_DIR/rpc_worker_${port}.log"
  lock_file="$RUN_DIR/rpc_worker_${port}.lock"
  acquire_lifecycle_lock "$lock_file"
  prepare_private_file "$log_file" "RPC Worker log"
  device=CPU
  [[ "$(platform_kind)" == jetson ]] && device=CUDA0
  command=(
    "$rpc_server_bin" --host "$host" --port "$port" --cache --device "$device"
  )
  stop_existing_before_start \
    "$pid_file" "$identity_file" "$rpc_server_bin" "${command[@]}"

  arm_candidate_rollback
  "${command[@]}" >"$log_file" 2>&1 9>&- &
  candidate_pid=$!
  RPC_ROLLBACK_PID="$candidate_pid"
  set +e
  guard_call record --pid "$candidate_pid" >/dev/null
  guard_status=$?
  set -e
  if [[ "$guard_status" -ne 0 ]]; then
    if ! cleanup_failed_candidate \
      "RPC Worker identity-capture rollback" "$candidate_pid"; then
      :
    fi
    die "RPC Worker identity capture failed"
  fi

  for _ in $(seq 1 100); do
    if ! candidate_is_managed "$candidate_pid"; then
      break
    fi
    if candidate_owns_port "$port" "$candidate_pid" \
      && candidate_is_managed "$candidate_pid" \
      && candidate_owns_port "$port" "$candidate_pid"; then
      disarm_candidate_rollback
      echo "[OK] RPC device started pid=$candidate_pid host=$host port=$port device=$device"
      return 0
    fi
    sleep 0.1
  done
  tail -n 80 "$log_file" >&2 || true
  if ! cleanup_failed_candidate "RPC Worker failed-start" "$candidate_pid"; then
    :
  fi
  die "RPC device failed to start"
}

start_coordinator() {
  local port=$1 model=$2 context=$3 gpu_layers=$4 endpoints=$5 split_mode=$6
  local split_csv=$7 bind_host=${8:-127.0.0.1}
  local pid_file identity_file log_file lock_file candidate_pid
  local guard_status observed_pid rollback_status
  local -a command
  validate_port "$port"
  require_lifecycle_tools
  require_readiness_tools
  command -v curl >/dev/null 2>&1 || die "curl is required for RPC coordinator health checks"
  check_runtime >/dev/null
  pid_file="$RUN_DIR/rpc_coordinator_${port}.pid"
  identity_file="$RUN_DIR/rpc_coordinator_${port}.identity.json"
  log_file="$RUN_DIR/rpc_coordinator_${port}.log"
  lock_file="$RUN_DIR/rpc_coordinator_${port}.lock"
  acquire_lifecycle_lock "$lock_file"
  prepare_private_file "$log_file" "RPC coordinator log"
  command=(
    "$llama_server_bin" --host "$bind_host" --port "$port"
    --model "$model" --ctx-size "$context" --gpu-layers "$gpu_layers"
    --rpc "$endpoints" --split-mode "$split_mode" --metrics
    --parallel 1 --cont-batching --no-webui
  )
  if [[ "$split_csv" != - ]]; then command+=(--tensor-split "$split_csv"); fi
  stop_existing_before_start \
    "$pid_file" "$identity_file" "$llama_server_bin" "${command[@]}"

  arm_candidate_rollback
  "${command[@]}" >"$log_file" 2>&1 9>&- &
  candidate_pid=$!
  RPC_ROLLBACK_PID="$candidate_pid"
  set +e
  guard_call record --pid "$candidate_pid" >/dev/null
  guard_status=$?
  set -e
  if [[ "$guard_status" -ne 0 ]]; then
    if ! cleanup_failed_candidate \
      "RPC coordinator identity-capture rollback" "$candidate_pid"; then
      :
    fi
    die "RPC coordinator identity capture failed"
  fi

  for _ in $(seq 1 1800); do
    if ! candidate_is_managed "$candidate_pid"; then
      break
    fi
    if candidate_owns_port "$port" "$candidate_pid" \
      && curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
      && candidate_is_managed "$candidate_pid" \
      && candidate_owns_port "$port" "$candidate_pid"; then
      disarm_candidate_rollback
      echo "[OK] RPC coordinator started pid=$candidate_pid port=$port"
      return 0
    fi
    sleep 0.25
  done
  tail -n 120 "$log_file" >&2 || true
  if ! cleanup_failed_candidate "RPC coordinator failed-start" "$candidate_pid"; then
    :
  fi
  die "RPC coordinator failed to load the model"
}

action="${1:-}"
case "$action" in
  prepare) prepare_runtime ;;
  check) check_runtime ;;
  start-worker) start_worker "${2:?port required}" "${3:-0.0.0.0}" ;;
  stop-worker)
    validate_port "${2:?port required}"
    stop_recorded_service \
      "$RUN_DIR/rpc_worker_${2}.pid" \
      "$RUN_DIR/rpc_worker_${2}.identity.json" \
      "$rpc_server_bin" \
      "$RUN_DIR/rpc_worker_${2}.lock"
    ;;
  start-coordinator) start_coordinator "${2:?}" "${3:?}" "${4:?}" "${5:?}" "${6:?}" "${7:?}" "${8:--}" "${9:-127.0.0.1}" ;;
  stop-coordinator)
    validate_port "${2:?port required}"
    stop_recorded_service \
      "$RUN_DIR/rpc_coordinator_${2}.pid" \
      "$RUN_DIR/rpc_coordinator_${2}.identity.json" \
      "$llama_server_bin" \
      "$RUN_DIR/rpc_coordinator_${2}.lock"
    ;;
  *) die "usage: runtime.sh prepare|check|start-worker|stop-worker|start-coordinator|stop-coordinator" ;;
esac
