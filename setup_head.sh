#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
RUNTIME_DIR="$PROJECT_ROOT/.run/cluster"
INVENTORY_FILE="$RUNTIME_DIR/nodes.local.csv"
IDENTITY_FILE="${CLUSTER_IDENTITY_FILE:-$HOME/.ssh/id_ed25519_llm_cluster}"

mkdir -p "$RUNTIME_DIR" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [[ ! -f "$INVENTORY_FILE" ]]; then
  cp "$SCRIPT_DIR/config/nodes.example.csv" "$INVENTORY_FILE"
  echo "[OK] created inventory: $INVENTORY_FILE"
else
  echo "[INFO] inventory already exists: $INVENTORY_FILE"
fi

if [[ ! -f "$IDENTITY_FILE" ]]; then
  ssh-keygen -t ed25519 -N "" -C "llm-cluster-head@$(hostname)" -f "$IDENTITY_FILE"
  echo "[OK] created cluster SSH identity: $IDENTITY_FILE"
else
  echo "[INFO] cluster SSH identity already exists: $IDENTITY_FILE"
fi

chmod 600 "$IDENTITY_FILE"
chmod 644 "$IDENTITY_FILE.pub"

echo
echo "Worker onboarding public key:"
cat "$IDENTITY_FILE.pub"
echo
echo "Install this key in each worker's ~/.ssh/authorized_keys, then register"
echo "the worker in the dashboard or edit $INVENTORY_FILE."
