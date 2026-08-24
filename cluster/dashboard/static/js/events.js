"use strict";

function authenticatedEventStream(path) {
  const controller = new AbortController();
  const stream = {
    closed: false,
    onopen: null,
    onerror: null,
    onmessage: null,
    close() {
      this.closed = true;
      controller.abort();
    },
  };

  (async () => {
    const headers = { Accept: "text/event-stream" };
    if (state.token) headers["X-Cluster-Token"] = state.token;
    const response = await fetch(path, { headers, signal: controller.signal, cache: "no-store" });
    if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
    stream.onopen?.();
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!stream.closed) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      blocks.forEach(block => {
        const data = block.split(/\r?\n/)
          .filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).trimStart())
          .join("\n");
        if (data) stream.onmessage?.({ data });
      });
      if (done) break;
    }
    if (!stream.closed) throw new Error("SSE connection closed");
  })().catch(error => {
    if (stream.closed || error?.name === "AbortError") return;
    stream.onerror?.(error);
    setTimeout(() => {
      if (state.eventSource === stream && !stream.closed) connectEvents();
    }, 1500);
  });
  return stream;
}
