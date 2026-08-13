#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODE="check"
PLAN_ONLY=0
PLATFORM_OVERRIDE=""

usage() {
  cat <<'EOF'
Usage: cluster/worker_setup.sh [--check-only|--install] [--project-dir PATH]
       cluster/worker_setup.sh --plan-only --platform jetson|raspberry-pi

Detects NVIDIA Jetson or Raspberry Pi 5, checks system dependencies and
verifies the matching llama-cpp-python backend. --install may install a fixed
apt package allowlist only when root or passwordless sudo is available. It
never accepts or stores a sudo/SSH password and never installs JetPack/CUDA.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) MODE="check"; shift ;;
    --install) MODE="install"; shift ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --plan-only) PLAN_ONLY=1; shift ;;
    --platform) PLATFORM_OVERRIDE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERROR] unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

read_board_model() {
  if [[ -r /proc/device-tree/model ]]; then
    tr -d '\000' </proc/device-tree/model
  else
    uname -m
  fi
}

detect_platform() {
  local override="${PLATFORM_OVERRIDE:-${CLUSTER_PLATFORM_OVERRIDE:-}}"
  local board
  board="$(read_board_model)"
  if [[ "$override" == "jetson" || "$override" == "raspberry-pi" ]]; then
    printf '%s' "$override"
  elif [[ -f /etc/nv_tegra_release || -d /etc/nv_tegra_release.d ]] || command -v nvpmodel >/dev/null 2>&1; then
    printf 'jetson'
  elif [[ "${board,,}" == *"raspberry pi"* ]]; then
    printf 'raspberry-pi'
  else
    printf 'unsupported'
  fi
}

PLATFORM_KIND="$(detect_platform)"
BOARD_MODEL="$(read_board_model)"
ARCH="$(uname -m)"
failures=0

fail() {
  echo "[FAIL] $*" >&2
  failures=$((failures + 1))
}

echo "[INFO] node=$(hostname) platform=$PLATFORM_KIND arch=$ARCH"
echo "[INFO] board=$BOARD_MODEL"
echo "[INFO] project=$PROJECT_DIR mode=$MODE"

if [[ "$PLAN_ONLY" -eq 0 && "$PLATFORM_KIND" == "unsupported" ]]; then
  fail "unsupported board; expected NVIDIA Jetson or Raspberry Pi"
fi
if [[ "$PLAN_ONLY" -eq 0 && "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
  fail "64-bit ARM OS is required (detected $ARCH)"
fi

common_packages=(
  ca-certificates curl git rsync openssh-client iproute2 build-essential cmake ninja-build pkg-config
  python3 python3-dev python3-venv
)
required_packages=("${common_packages[@]}")
if [[ "$PLATFORM_KIND" == "raspberry-pi" ]]; then
  required_packages+=(libopenblas-dev)
fi
if [[ "$PLAN_ONLY" -eq 1 ]]; then
  echo "[PLAN] platform=$PLATFORM_KIND"
  echo "[PLAN] apt=${required_packages[*]}"
  if [[ "$PLATFORM_KIND" == "jetson" ]]; then
    echo "[PLAN] backend=cuda"
    echo "[PLAN] cmake=-DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=87"
  elif [[ "$PLATFORM_KIND" == "raspberry-pi" ]]; then
    echo "[PLAN] backend=openblas n_gpu_layers=0"
    echo "[PLAN] cmake=-DGGML_NATIVE=ON -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS -DCMAKE_BUILD_TYPE=Release"
  else
    fail "unsupported platform plan"
    exit 1
  fi
  exit 0
fi
missing_packages=()
venv_probe_dir=""
venv_works=0
if command -v python3 >/dev/null 2>&1; then
  venv_probe_dir="$(mktemp -d)"
  if python3 -m venv "$venv_probe_dir/check" >/dev/null 2>&1; then
    venv_works=1
  fi
  rm -rf "$venv_probe_dir"
fi
if command -v dpkg-query >/dev/null 2>&1; then
  for package_name in "${required_packages[@]}"; do
    if [[ "$package_name" == "python3-venv" && "$venv_works" -eq 1 ]]; then
      continue
    fi
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "$package_name" 2>/dev/null | grep -q '^ii'; then
      missing_packages+=("$package_name")
    fi
  done
else
  fail "dpkg-query is unavailable; Debian/Ubuntu based 64-bit OS is required"
fi

if (( ${#missing_packages[@]} > 0 )); then
  if [[ "$MODE" == "install" ]]; then
    apt_prefix=()
    if [[ "$(id -u)" -ne 0 ]]; then
      if sudo -n true >/dev/null 2>&1; then
        apt_prefix=(sudo -n)
      else
        fail "system packages require sudo. Run once on the worker: sudo apt-get update && sudo apt-get install -y ${missing_packages[*]}"
      fi
    fi
    if (( failures == 0 )); then
      echo "[INFO] installing fixed system package allowlist: ${missing_packages[*]}"
      "${apt_prefix[@]}" apt-get update
      "${apt_prefix[@]}" apt-get install -y --no-install-recommends "${missing_packages[@]}"
    fi
  else
    fail "missing system packages: ${missing_packages[*]}"
  fi
else
  echo "[OK] system build dependencies"
fi

if [[ "$PLATFORM_KIND" == "jetson" ]]; then
  if [[ -x /usr/local/cuda/bin/nvcc ]]; then
    echo "[OK] CUDA compiler: $(/usr/local/cuda/bin/nvcc --version | tail -n 1)"
  else
    fail "JetPack/CUDA is missing; install the matching NVIDIA JetPack image manually"
  fi
  if command -v nvpmodel >/dev/null 2>&1; then
    power_mode="$(nvpmodel -q 2>/dev/null | head -n 1 || true)"
    echo "[OK] nvpmodel: ${power_mode:-available}"
  else
    fail "nvpmodel is missing; verify the JetPack installation"
  fi
elif [[ "$PLATFORM_KIND" == "raspberry-pi" ]]; then
  if [[ "${BOARD_MODEL,,}" != *"raspberry pi 5"* ]]; then
    echo "[WARN] optimized target is Raspberry Pi 5; detected $BOARD_MODEL"
  fi
  echo "[OK] Raspberry Pi CPU/OpenBLAS runtime selected (GPU layers must be 0)"
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  fail "project directory is missing: $PROJECT_DIR"
fi

if [[ "$MODE" == "install" && ! -x "$PROJECT_DIR/.venv/bin/python" && $failures -eq 0 ]]; then
  echo "[INFO] creating Python virtual environment"
  python3 -m venv "$PROJECT_DIR/.venv"
fi

python_bin="$PROJECT_DIR/.venv/bin/python"
if [[ -x "$python_bin" ]]; then
  echo "[OK] virtual environment: $($python_bin --version 2>&1)"
  common_imports='import fastapi, uvicorn, psutil, jinja2, sse_starlette'
  runtime_requirements="$PROJECT_DIR/cluster/requirements-runtime.txt"
  runtime_versions_ok() {
    "$python_bin" - "$runtime_requirements" <<'PY'
import sys
from importlib.metadata import PackageNotFoundError, version

for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    package, expected = line.split("==", 1)
    try:
        actual = version(package)
    except PackageNotFoundError:
        raise SystemExit(1)
    if actual != expected:
        print(f"{package}: expected {expected}, found {actual}", file=sys.stderr)
        raise SystemExit(1)
PY
  }
  if ! "$python_bin" -c "$common_imports" >/dev/null 2>&1 || ! runtime_versions_ok; then
    if [[ "$MODE" == "install" ]]; then
      echo "[INFO] installing pinned common Python dependencies"
      "$python_bin" -m pip install --upgrade pip setuptools wheel
      "$python_bin" -m pip install --requirement "$runtime_requirements"
    else
      fail "required Python packages are missing or differ from cluster/requirements-runtime.txt"
    fi
  else
    echo "[OK] common Python runtime packages"
  fi

  if [[ "$PLATFORM_KIND" == "jetson" ]] && ! "$python_bin" -c 'import jtop' >/dev/null 2>&1; then
    if [[ "$MODE" == "install" ]]; then
      "$python_bin" -m pip install 'jetson-stats==4.3.2'
    else
      fail "jetson-stats/jtop is missing from the virtual environment"
    fi
  fi
  if [[ "$PLATFORM_KIND" == "jetson" ]] && command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet jtop.service; then
    echo "[WARN] jtop.service is not active; psutil telemetry works, but advanced Jetson metrics require a system-level jetson-stats service setup"
  fi

  verify_backend() {
    CLUSTER_EXPECTED_PLATFORM="$PLATFORM_KIND" "$python_bin" - <<'PY'
import os
import subprocess
from pathlib import Path
import llama_cpp as llama_package
from llama_cpp import llama_cpp

expected = os.environ["CLUSTER_EXPECTED_PLATFORM"]
info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
gpu = bool(llama_cpp.llama_supports_gpu_offload())
print(" ".join(info.split())[:800])
if getattr(llama_package, "__version__", "") != "0.3.20":
    raise SystemExit(1)
if expected == "jetson":
    raise SystemExit(0 if gpu and "CUDA" in info.upper() else 1)
if expected == "raspberry-pi":
    library_root = Path(llama_package.__file__).resolve().parent
    linked_openblas = False
    for candidate in library_root.rglob("*.so"):
        try:
            linked = subprocess.check_output(["ldd", str(candidate)], text=True, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        if "openblas" in linked.lower():
            linked_openblas = True
            break
    normalized = " ".join(info.upper().split())
    arm_optimized = "NEON = 1" in normalized and "ARM_FMA = 1" in normalized
    raise SystemExit(0 if not gpu and linked_openblas and arm_optimized else 1)
raise SystemExit(1)
PY
  }

  backend_log="$(mktemp)"
  if verify_backend >"$backend_log" 2>&1; then
    echo "[OK] llama-cpp-python backend verified for $PLATFORM_KIND"
  elif [[ "$MODE" == "install" && $failures -eq 0 ]]; then
    echo "[INFO] building llama-cpp-python 0.3.20 for $PLATFORM_KIND"
    if [[ "$PLATFORM_KIND" == "jetson" ]]; then
      PATH="/usr/local/cuda/bin:$PATH" \
      CUDACXX="/usr/local/cuda/bin/nvcc" \
      CMAKE_ARGS="-DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=87" \
      FORCE_CMAKE=1 "$python_bin" -m pip install --force-reinstall --no-cache-dir \
        --no-binary=llama-cpp-python 'llama-cpp-python==0.3.20'
    elif [[ "$PLATFORM_KIND" == "raspberry-pi" ]]; then
      CMAKE_ARGS="-DGGML_NATIVE=ON -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS -DCMAKE_BUILD_TYPE=Release" \
      FORCE_CMAKE=1 "$python_bin" -m pip install --force-reinstall --no-cache-dir \
        --no-binary=llama-cpp-python 'llama-cpp-python==0.3.20'
    fi
    if verify_backend >"$backend_log" 2>&1; then
      echo "[OK] llama-cpp-python backend installed and verified"
    else
      fail "llama-cpp-python backend verification failed after build"
      sed -n '1,12p' "$backend_log" >&2 || true
    fi
  else
    fail "llama-cpp-python backend is missing or does not match $PLATFORM_KIND"
    sed -n '1,12p' "$backend_log" >&2 || true
  fi
  rm -f "$backend_log"
else
  fail "virtual environment is missing: $PROJECT_DIR/.venv"
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
echo "[OK] worker is ready platform=$PLATFORM_KIND backend=$([[ "$PLATFORM_KIND" == "jetson" ]] && echo cuda || echo openblas)"
