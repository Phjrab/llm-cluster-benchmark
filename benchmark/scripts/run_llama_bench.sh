#!/usr/bin/env bash
set -euo pipefail

# 노드에서 실행한다. 아래 세 값은 해당 노드 환경에 맞게 반드시 수정한다.
MODEL="/opt/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
LLAMA_BENCH="/opt/llama.cpp/build/bin/llama-bench"
OUT_DIR="./results"
PLATFORM="raspberry-pi" # 예: jetson-orin-nano
NODE_NAME="node-01"
# Pi 예: EXTRA_ARGS="-t 4" / Jetson 예: EXTRA_ARGS="-ngl 999"
EXTRA_ARGS="-t 4"

mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/${PLATFORM}_${NODE_NAME}_$(date +%Y%m%d_%H%M%S).csv"

if [[ ! -x "$LLAMA_BENCH" ]]; then
  echo "llama-bench를 찾을 수 없습니다: $LLAMA_BENCH" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "모델을 찾을 수 없습니다: $MODEL" >&2
  exit 1
fi

echo "platform,node,run,pp,tg,raw_output" > "$OUT_FILE"
for run in 1 2 3 4 5; do
  for spec in "128 128" "512 128" "512 512"; do
    read -r pp tg <<< "$spec"
    echo "[$NODE_NAME] run=$run pp=$pp tg=$tg"
    # --csv 출력 형식은 llama.cpp 버전에 따라 다를 수 있어 원문도 함께 보관한다.
    output=$("$LLAMA_BENCH" -m "$MODEL" -p "$pp" -n "$tg" $EXTRA_ARGS --csv 2>&1 || true)
    escaped=$(printf '%s' "$output" | tr '\n' ' ' | sed 's/"/""/g')
    printf '%s,%s,%s,%s,%s,"%s"\n' "$PLATFORM" "$NODE_NAME" "$run" "$pp" "$tg" "$escaped" >> "$OUT_FILE"
    sleep 30
  done
done

echo "저장 완료: $OUT_FILE"
