# Performance

Measured on Linux with Hermes Agent v0.21.4, `agy` 1.2.11 and `gemini-3.8-flash-low`, driving real `hermes chat -q … --provider antigravity-agy` runs in isolated `HERMES_HOME`s with [`scripts/bench.py`](../scripts/bench.py). Each configuration ran every task 3 times.

## 1. Result

| Metric (mean)                           | Before | After  | Change |
| --------------------------------------- | ------ | ------ | ------ |
| Two-step tool task, total wall time     | 44.5 s | 30.2 s | −32%   |
| Tool task, latency per request          | 10.3 s | 5.5 s  | −47%   |
| Tool task, waiting for agy startup      | 5.7 s  | 2.1 s  | −63%   |
| Single-turn Q&A, total wall time        | 25.8 s | 22.1 s | −14%   |
| Single-turn Q&A, latency of the request | 13.6 s | 9.4 s  | −31%   |
| Input tokens per request (tool task)    | 16,413 | 14,628 | −11%   |
| Trace-audit violations                  | 0      | 0      | —      |

Tasks: `qa` asks one question; `tool` asks Hermes to `read_file` a file and then run `wc -l` on it with `terminal` (three model requests). All answers were correct before and after.

## 2. Where the time goes

1. **Hermes startup, ~11 s**, from launching `hermes chat` to its first model request. Outside the plugin.
2. **agy startup, ~5–6 s per request**, mostly network round trips (`loadCodeAssist`, `fetchAvailableModels`). agy has no flag to skip them.
3. **agy boots before it reads its prompt.** It emits `init` before anything is written to stdin, and answers ~2.7 s after the prompt arrives. This is what makes pre-warming possible.
4. **Shutdown wait, ~1–3 s**: the original code waited for agy to exit before handing over the final answer.
5. **~16k input tokens per request**: Hermes' system prompt (~17.5k characters) plus the catalog of ~23 tools (~40k characters, indented JSON).

## 3. Adopted

### 3.1 Pre-warmed agy processes

While a request with tools runs, the plugin starts spare agy processes for the next request of the same shape (resolved argv, model, relay system prompt, tool list, environment). The next request in the Hermes tool loop takes a spare and skips agy's startup.

| Configuration                           | Tool task total | Request 2 startup / latency | Request 3 startup / latency |
| --------------------------------------- | --------------- | --------------------------- | --------------------------- |
| Before                                  | 44.5 s          | 5.3 s / 8.8 s               | 6.6 s / 12.9 s              |
| Depth 1 + background shutdown           | 34.0 s          | 0 / 3.3 s                   | 1.5–1.8 s / 4.2–7.9 s       |
| Depth 2 (default) + background shutdown | 30.4 s          | 0 / 2.5–3.3 s               | 0 / 2.6–3.1 s               |

With depth 1 the spare for request 3 only starts during the short request 2 and is ~1.7 s short; depth 2 boots both spares during the longer cold first request.

- Only requests that carry tools pre-warm (auxiliary requests such as title generation do not).
- Up to `ANTIGRAVITY_AGY_PREWARM` spares per shape (default 2), 4 in total; unused spares die after `ANTIGRAVITY_AGY_PREWARM_TTL` seconds (default 300) and at interpreter exit. If the Hermes process is killed, a spare sees its stdin close and exits within ~1 s.
- **Invariant:** a spare has received nothing on stdin, so it has made no model call and cannot have seen a tool result. After takeover it gets exactly one prompt and behaves like a cold start: the first `call_mcp_tool -> hermes/<tool>` kills its process group.
- **Cost:** an idle spare process group uses ~416 MB RSS. Set `ANTIGRAVITY_AGY_PREWARM=1` or `0` where memory is tight.

### 3.2 Background shutdown

After a successful `result`, the final chunk goes to Hermes first; waiting for agy to exit (up to 15 s, then kill) happens in a background thread, and exit handling finishes pending shutdowns. The gap between agy's last output and the answer reaching Hermes dropped from ~3 s to ~0.1 s.

### 3.3 Compact tool-schema JSON

The tool catalog in the relay prompt is serialized without indentation (`separators=(',', ':')`): same content, input tokens 16,092 → 14,307 (qa) and 16,413 → 14,628 (tool), −11%.

### 3.4 Conversation-record cleanup

`ANTIGRAVITY_AGY_CLEANUP=unused` (default) moves the agy conversation records of spares that never received a prompt to the trash; `all` covers every plugin run; `off` disables it. Only files named by the conversation id from that process's own `init` event are touched, with `trash-put`; shared stores are never modified.

## 4. Rejected

| Idea                                                                                   | Measurement                                                                  | Why rejected                                                                                        |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| One long-lived agy for several turns (`--input-format stream-json`) / `--conversation` | Second turn 2.1 s                                                            | No faster than a spare; would feed tool results into agy and keep a history that forks from Hermes' |
| Tool catalog first in the prompt, for cross-session cache reuse                        | 0/12 benchmark requests hit, same as before                                  | No measurable gain                                                                                  |
| Startup flags                                                                          | None exist in `agy --help` / `agy changelog`                                 | —                                                                                                   |
| Delay the kill after a captured tool call, to help caching                             | 0/6 cache hits with 0 s delay and 0/6 with 2 s delay in benchmark tool loops | No measurable gain; every tool request keeps a ~400 MB agy alive longer                             |

## 5. Prompt caching: benchmarks vs real use

Benchmark tool loops (three ~15k-token requests 2–5 s apart) never hit agy's prompt cache. Real use does:

| Real Hermes requests (139, mostly one long gateway session) | Requests | With cache hit | Token hit rate |
| ----------------------------------------------------------- | -------- | -------------- | -------------- |
| All                                                         | 139      | 130            | 88.9%          |
| First request of a turn                                     | 9        | 2              | 45.8%          |
| Later requests in a tool loop                               | 130      | 128            | 90.3%          |

Controlled experiments showed:

- The workspace path is not part of the prompt; a fixed cwd and a fresh cwd per request hit identically.
- When the previous request finished normally, the next one with the same system prompt hit 18/18 times.
- After a request killed at a tool call, hits ranged from 0/2 (immediate kill) to 4/4 (delayed kill) with a cold next request, and 2/4 with a pre-warmed next request, independent of a 0.3 s vs 6 s gap.

The likely, **unverified** explanation for the benchmark misses is that the server needs a few seconds before a new cache entry can be reused, and the benchmark's turns are too close together. Judge caching by real session logs (Hermes logs `cache=<hit>/<input>` per request), not by the benchmark.

## 6. Reproducing

```bash
# needs a signed-in agy and Hermes on PATH; spends a little subscription quota
python scripts/bench.py --plugin-dir . --runs 3 --out bench-before.json
# … change the plugin …
python scripts/bench.py --plugin-dir . --runs 3 --out bench-after.json
python scripts/bench.py --compare bench-before.json bench-after.json
```

`bench.py` creates a fresh isolated Hermes home per run, sets `ANTIGRAVITY_AGY_TRACE_DIR`, derives per-request timings (startup, first output, latency) and token counts from the trace, runs the trace audit and checks that no agy process outlives Hermes.
