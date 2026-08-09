#!/usr/bin/env python3
"""OpenAI 호환 /v1/chat/completions 엔드포인트의 간단한 부하 측정기."""
import argparse
import asyncio
import csv
import json
import statistics
import time
import urllib.request

PROMPT = "다음 문장을 한 문단으로 설명해줘: 엣지 장치에서 LLM을 실행한다."

def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * p
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)

def request(url, model, max_tokens):
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": PROMPT}],
                          "max_tokens": max_tokens, "temperature": 0, "stream": True,
                          "stream_options": {"include_usage": True}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            first_token_at = None
            chunks = 0
            tokens = None
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                body = line[6:]
                if body == "[DONE]":
                    break
                event = json.loads(body)
                if event.get("usage", {}).get("completion_tokens") is not None:
                    tokens = event["usage"]["completion_tokens"]
                choice = event.get("choices", [{}])[0]
                if choice.get("delta", {}).get("content"):
                    chunks += 1
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
        elapsed = time.perf_counter() - start
        ttft = (first_token_at - start) if first_token_at else elapsed
        # 일부 서버는 stream_options를 지원하지 않는다. 그 경우 chunk 수를 보조값으로 쓴다.
        tokens = tokens if tokens is not None else chunks
        generation_s = max(elapsed - ttft, 1e-9)
        return {"ok": True, "ttft_s": ttft, "e2e_s": elapsed, "completion_tokens": tokens,
                "generation_tok_s": tokens / generation_s, "error": ""}
    except Exception as exc:
        return {"ok": False, "ttft_s": 0, "e2e_s": time.perf_counter() - start, "completion_tokens": 0,
                "generation_tok_s": 0, "error": str(exc)}

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="예: http://proxy.local:8080/v1/chat/completions")
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--output", default="load_results.csv")
    args = parser.parse_args()
    semaphore = asyncio.Semaphore(args.concurrency)
    async def one(index):
        async with semaphore:
            result = await asyncio.to_thread(request, args.url, args.model, args.max_tokens)
            result["request"] = index
            return result
    started = time.perf_counter()
    results = await asyncio.gather(*(one(i + 1) for i in range(args.requests)))
    wall_s = time.perf_counter() - started
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=results[0].keys())
        writer.writeheader(); writer.writerows(results)
    success = [r for r in results if r["ok"]]
    e2e = [r["e2e_s"] for r in success]
    ttft = [r["ttft_s"] for r in success]
    total_tokens = sum(r["completion_tokens"] for r in success)
    print(f"success={len(success)}/{len(results)} wall_s={wall_s:.2f} cluster_tok_s={total_tokens / wall_s:.2f}")
    if e2e:
        print(f"ttft_p50={percentile(ttft, .50):.2f}s ttft_p95={percentile(ttft, .95):.2f}s")
        print(f"e2e_p50={percentile(e2e, .50):.2f}s e2e_p95={percentile(e2e, .95):.2f}s mean={statistics.mean(e2e):.2f}s")
    print(f"saved={args.output}")

if __name__ == "__main__":
    asyncio.run(main())
