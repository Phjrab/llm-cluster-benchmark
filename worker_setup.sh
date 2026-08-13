#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$DEFAULT_PROJECT_DIR"
CHECK_ONLY=0
INSTALL=0

usage() {
  cat <<'EOF'
Usage: cluster/worker_setup.sh [--check-only|--install] [--project-dir PATH]

Checks a Jetson worker for the runtime required by the cluster benchmark.
--install creates the virtual environment, installs the Python dependencies,
and builds llama-cpp-python 0.3.20 with CUDA. System/CUDA packages are never
installed automatically.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --install)
      INSTALL=1
      shift
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$CHECK_ONLY" -eq 0 && "$INSTALL" -eq 0 ]]; then
  CHECK_ONLY=1
fi
if [[ "$CHECK_ONLY" -eq 1 && "$INSTALL" -eq 1 ]]; then
  echo "[ERROR] --check-only and --install are mutually exclusive" >&2
  exit 2
fi

failures=0
check_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    echo "[OK] command $command_name: $(command -v "$command_name")"
  else
    echo "[FAIL] missing command: $command_name" >&2
    failures=$((failures + 1))
  fi
}

echo "[INFO] node=$(hostname) project=$PROJECT_DIR"
for required in python3 git rsync curl; do
  check_command "$required"
done

if [[ ! -d /etc/nv_tegra_release.d && ! -f /etc/nv_tegra_release ]]; then
  echo "[WARN] NVIDIA Jetson release metadata was not found"
fi

if command -v nvpmodel >/dev/null 2>&1; then
  power_mode="$(nvpmodel -q 2>/dev/null | head -n 1 || true)"
  echo "[OK] nvpmodel: ${power_mode:-available}"
else
  echo "[FAIL] nvpmodel is missing" >&2
  failures=$((failures + 1))
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "[FAIL] project directory is missing: $PROJECT_DIR" >&2
  failures=$((failures + 1))
fi

if [[ "$INSTALL" -eq 1 && ! -d "$PROJECT_DIR/.venv" ]]; then
  echo "[INFO] creating virtual environment"
  python3 -m venv "$PROJECT_DIR/.venv"
fi

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  python_bin="$PROJECT_DIR/.venv/bin/python"
  echo "[OK] virtual environment: $($python_bin --version 2>&1)"
  if ! "$python_bin" -c 'import fastapi, uvicorn, psutil, llama_cpp' >/dev/null 2>&1; then
    if [[ "$INSTALL" -eq 0 ]]; then
      echo "[FAIL] required Python packages are missing" >&2
      failures=$((failures + 1))
    else
      echo "[INFO] installing common Python dependencies"
      "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip setuptools wheel
      "$PROJECT_DIR/.venv/bin/pip" install \
        'psutil>=5.9' 'huggingface_hub>=0.23' 'matplotlib>=3.8' \
        'fastapi>=0.111' 'uvicorn>=0.30' 'jinja2>=3.1' 'sse-starlette>=2.1'
    fi
  else
    echo "[OK] Python runtime packages"
  fi

  cuda_check_file="$(mktemp)"
  if "$python_bin" - <<'PY' >"$cuda_check_file" 2>&1
from llama_cpp import llama_cpp
info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
print(info)
raise SystemExit(0 if "CUDA" in info else 1)
PY
  then
    echo "[OK] llama-cpp-python CUDA backend"
  else
    if [[ "$INSTALL" -eq 1 ]]; then
      echo "[INFO] building llama-cpp-python 0.3.20 with CUDA"
      CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_BUILD_TYPE=Release" \
        FORCE_CMAKE=1 "$PROJECT_DIR/.venv/bin/pip" install \
        --force-reinstall --no-cache-dir --no-binary=llama-cpp-python \
        'llama-cpp-python==0.3.20'
      if "$python_bin" - <<'PY' >"$cuda_check_file" 2>&1
from llama_cpp import llama_cpp
info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
print(info)
raise SystemExit(0 if "CUDA" in info else 1)
PY
      then
        echo "[OK] llama-cpp-python CUDA backend installed"
      else
        echo "[FAIL] CUDA backend verification failed after build" >&2
        sed -n '1,12p' "$cuda_check_file" >&2 || true
        failures=$((failures + 1))
      fi
    else
      echo "[FAIL] llama-cpp-python is not CUDA-enabled" >&2
      sed -n '1,12p' "$cuda_check_file" >&2 || true
      failures=$((failures + 1))
    fi
  fi
  rm -f "$cuda_check_file"
else
  echo "[FAIL] virtual environment is missing: $PROJECT_DIR/.venv" >&2
  failures=$((failures + 1))
fi

model_count=0
if [[ -d "$PROJECT_DIR/models" ]]; then
  model_count="$(find "$PROJECT_DIR/models" -type f -name '*.gguf' | wc -l | tr -d ' ')"
fi
echo "[INFO] GGUF models: $model_count"

if (( failures > 0 )); then
  echo "[FAIL] worker preflight found $failures problem(s)" >&2
  exit 1
fi
echo "[OK] worker is ready"
