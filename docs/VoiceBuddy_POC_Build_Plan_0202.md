# VoiceBuddy — POC Build Plan

**Companion to:** VoiceBuddy Technical Deep Dive
**Date:** February 2026
**Purpose:** Scope definition + phased build sequence for the POC. Handed off to Claude Code as-is.

---

## 1. Problem Statement

Small service businesses — realtors, plumbers, electricians, HVAC companies, law firm secretaries, dental offices, reception desks — miss inbound calls every day. A missed call is a missed customer. Hiring a receptionist to cover every line is expensive and does not scale. Existing robotic phone trees frustrate callers and erode trust in the business.

VoiceBuddy is an AI-powered phone assistant that answers calls on behalf of these businesses. It is not a chatbot transplanted onto a phone line. It is purpose-built for phone: it listens, responds in under a second, handles interruptions naturally, and captures the information the business needs — all without the caller realising they are talking to a machine.

The core bet: if latency is low enough and turn-taking is natural enough, callers will not notice the difference in the first 30 seconds of a call. That window is all we need to qualify the need, collect information, and route or schedule.

---

## 2. Scope

### 2.1 What This POC Proves

1. End-to-end latency can hit < 1,000 ms at p75 — fast enough to feel like a person.
2. Barge-in (interruption) works reliably — the bot stops mid-sentence when the caller talks, every time.
3. The conversation stays on-task for structured flows (qualify → capture → schedule) without derailing.

### 2.2 In Scope

- Browser-based POC using WebRTC mic/speaker. Phone via Twilio is the production target but is NOT the POC transport.
- Single business scenario: one fictitious service business (e.g. an HVAC company). One scripted flow, fully exercised.
- Chained pipeline: Deepgram Flux (STT) → Claude Haiku 4.5 + Sonnet 4.5 (LLM) → Cartesia Sonic 3 (TTS).
- Dual-layer LLM: Haiku fires fillers instantly while Sonnet generates the full response in parallel.
- Barge-in detection: three-layer stack (echo cancellation → VAD → STT confirmation).
- Latency instrumentation at every pipeline stage, present from Day 1.
- Voice cloning: one cloned "friendly receptionist" voice via Cartesia (requires 30–60 s of recorded audio).

### 2.3 Out of Scope

- Phone integration (Twilio). Architecture is server-first to support it later, but it is not built here.
- Multi-business or multi-scenario flows.
- Production hardening: auth, billing, multi-tenancy, monitoring dashboards.
- Real-time backchanneling ("mm-hmm" mid-stream). Achieved through LLM response phrasing instead.
- Eager End-of-Turn / speculative firing. The architecture supports it via a config toggle, but the POC baseline runs EndOfTurn-only.
- Multilingual support. Flux is English-only; this is a known POC constraint.

---

## 3. Technical Decisions (Fixed — Do Not Revisit)

These are resolved in the Technical Deep Dive. The build plan assumes them as constraints.

| Decision | Answer | Why |
|---|---|---|
| STT | Deepgram Flux | Built-in turn-taking, Eager EOT exclusive, fastest TTFT in independent benchmarks (Coval). Requires `/v2/listen` endpoint, 80 ms chunk size. |
| TTS (primary) | Cartesia Sonic 3 | Lowest model latency: 40–90 ms TTFA. Supports voice cloning from 10 s of audio. |
| TTS (fallback) | ElevenLabs Flash 2.5 | Best voice quality in the industry. 75–100 ms TTFA. Use if Cartesia has an outage or quality issue. |
| LLM | Claude Haiku 4.5 (fillers) + Claude Sonnet 4.5 (full replies) | Dual-layer strategy. Haiku sub-150 ms for fillers; Sonnet for natural full answers. Prompt caching on the Anthropic API cuts TTFT after the first turn. |
| Connection topology | Separate persistent WebSocket per service | Provider-agnostic, independently debuggable. 20–50 ms overhead vs a single socket is negligible against 100–200 ms of phone-network jitter. |
| Where logic lives | Server. Client is dumb audio I/O only. | Phone integration (Twilio) has no client. VAD, barge-in, LLM orchestration, TTS streaming all run server-side. Browser just captures mic and plays back. |
| Session state store | In-memory + Redis (short TTL) | In-memory alone fails on server restart mid-call. Redis TTL = call duration + small buffer. No database. |
| Echo cancellation | WebRTC AEC (browser) / RNNoise on server (phone, later) | WebRTC handles it automatically for the POC. Server-side canceller is the phone-phase plan. |

### 3.1 Latency Budget (Target: < 1,000 ms realistic)

| Stage | Best | Realistic | Worst | Service |
|---|---|---|---|---|
| 1 — End-of-Speech Detection | 150 ms | 250 ms | 400 ms | Deepgram Flux EOT |
| 2 — Final Transcript Return | 50 ms | 100 ms | 200 ms | Deepgram Flux |
| 3 — LLM First Token | 100 ms | 250 ms | 500 ms | Haiku / Sonnet |
| 4 — TTS First Audio Byte | 40 ms | 90 ms | 200 ms | Cartesia Sonic 3 |
| 5 — Network + Playback | 30 ms | 80 ms | 150 ms | WebSocket |
| **TOTAL** | **370 ms** | **770 ms** | **1,450 ms** | |

### 3.2 Conversation State Machine

The system is always in exactly one state. Transitions are event-driven.

| State | Trigger In | Action |
|---|---|---|
| IDLE | Call starts / user goes silent long-term | VAD monitoring ON |
| USER_SPEAKING | VAD detects speech | Stream audio to STT |
| PROCESSING | EndOfTurn fires, transcript ready | Send to LLM; start filler timer |
| FILLER_RESPONSE | LLM has not returned within 500 ms | Play filler ("Sure, let me check on that…") |
| BOT_SPEAKING | TTS audio ready | Play audio; keep VAD running |
| BARGE_IN_DETECTED | Speech confirmed while BOT_SPEAKING | Stop TTS immediately (mid-sentence), cancel LLM if in-flight, clear TTS buffer, → USER_SPEAKING |

### 3.3 Silence Policy

| Duration after user stops | Action |
|---|---|
| 0–500 ms | Normal processing window. Filler fires here if LLM hasn't returned. |
| 500 ms–2 s | Inject filler if one hasn't played: "Just a moment…" |
| 2–5 s | Something is wrong. Prompt: "Are you still there?" |
| 5+ s | Assume call dropping. One final prompt, then graceful close. |

### 3.4 Cost Estimate

| Service | Model | Est. $/hr |
|---|---|---|
| STT — Deepgram | Flux | $0.015 |
| LLM — Anthropic | Haiku (filler) + Sonnet (main), blended | $0.08–0.40 |
| TTS — Cartesia | Sonic 3 | $0.10–0.20 |
| **TOTAL** | | **$0.21–0.65 / hr (~$0.004–0.011 / min)** |

A 5-minute call costs $0.02–0.055. Unit economics are healthy at any price point above $1/call.

---

## 4. Build Phases

Seven phases. Each has an exit gate — a measurable pass/fail condition. Nothing moves forward until the gate passes. The sequence is driven by dependencies.

### Phase 1 — Latency Harness + Service Accounts
**Days 1–2**

> **Exit Gate:** Latency logger compiles, runs, and writes timestamped JSON to disk. All three service accounts (Deepgram, Anthropic, Cartesia) are active and key-accessible from the server.

This is the prerequisite for everything downstream. The harness is not a nice-to-have — it is the instrument panel. Every exit gate in every later phase is measured through it. Service accounts can take hours to provision; start them on Day 1 in parallel with coding.

Tasks:
1. Provision Deepgram account. Obtain Flux-enabled API key. Confirm `/v2/listen` endpoint is accessible.
2. Provision Anthropic account. Obtain API key. Confirm access to both `claude-haiku-4-5-20251001` and `claude-sonnet-4-5-20250929`.
3. Provision Cartesia account. Obtain API key. Confirm Sonic 3 model is available.
4. Build the latency logger. It timestamps at five points: mic capture, server receipt, STT result, LLM first token, TTS first byte. Output: append-only JSON log file. No dashboard yet — that comes later.
5. Record 30–60 seconds of "friendly receptionist" voice audio for Cartesia cloning. Submit the cloning request immediately — it has an async processing delay.

### Phase 2 — State Machine Skeleton
**Days 2–3**

> **Exit Gate:** The state machine compiles, transitions correctly between all six states when fed synthetic events (no real audio yet), and logs every transition with a timestamp.

The state machine is the skeleton. Every piece of code that follows — STT integration, LLM calls, TTS playback, barge-in — plugs into it as an event source or a state-transition handler. Build it first, test it in isolation with fake events, then wire real services into it one at a time.

Tasks:
1. Implement the six-state machine (IDLE, USER_SPEAKING, PROCESSING, FILLER_RESPONSE, BOT_SPEAKING, BARGE_IN_DETECTED) as defined in Section 3.2.
2. Wire it into the latency logger from Phase 1. Every transition logs its state, the triggering event, and a timestamp.
3. Write a synthetic event driver: a script that fires a scripted sequence of events (e.g. "speech detected → end of turn → LLM ready → TTS ready → silence") and prints the state trace. This is your test harness for the state machine — keep it. You will use it again.
4. Verify all transitions match the spec. No ambiguous states. No missing transitions.

### Phase 3 — Audio Pipe (No AI Yet)
**Days 3–5**

> **Exit Gate:** Speak into the browser mic. The audio arrives at the server, is echoed back via WebSocket, and plays through the browser speaker. End-to-end round-trip latency is logged and is under 200 ms.

This phase proves the transport layer works before any AI touches the audio. It is deceptively important: audio format mismatches, WebSocket buffering bugs, and codec issues all live here. Kill them now, not after you have layered STT and TTS on top.

Tasks:
1. Set up the browser client: capture mic audio via the Web Audio API (or MediaRecorder), stream it over a WebSocket to the server.
2. Set up the server: receive the WebSocket audio stream, log receipt timestamp, echo the raw audio back over a second WebSocket.
3. Set up the browser playback: receive the echoed audio stream and play it through the speaker.
4. Confirm the latency logger captures the round-trip (mic capture → server receipt → server send-back → browser playback). Target: under 200 ms.
5. Confirm WebRTC's built-in AEC is active (it should be by default). If you hear echo, stop and debug before moving on.

### Phase 4 — Add STT (Deepgram Flux)
**Days 5–7**

> **Exit Gate:** Speak a sentence into the mic. The server receives the transcript via Deepgram Flux within 350 ms of end-of-speech (p50). The EndOfTurn event fires correctly. The latency logger captures Stages 1 and 2 of the budget.

This is the first real service integration. Flux replaces the need for a separate VAD + endpointing layer — it emits structured turn events directly. Do not build your own VAD on top of it; trust the events.

Tasks:
1. Replace the raw audio echo from Phase 3 with a Deepgram Flux streaming connection. Use the `/v2/listen` endpoint, 80 ms chunk size.
2. Log the Flux events: StartOfTurn, EndOfTurn, and (when ready to test later) EagerEndOfTurn. Timestamp each one.
3. Print the final transcript to the server console on EndOfTurn. Visually confirm it is accurate.
4. Update the state machine: VAD/speech detection now comes from Flux's StartOfTurn. End-of-speech comes from EndOfTurn. Wire these into the USER_SPEAKING → PROCESSING transition.
5. Measure Stage 1 (end-of-speech detection) and Stage 2 (transcript return) latencies against the budget. If p50 exceeds 350 ms, investigate before moving on.

### Phase 5 — Add LLM (Dual-Layer: Haiku + Sonnet)
**Days 7–9**

> **Exit Gate:** Speak a question. Within 600 ms of end-of-speech, a filler ("Sure, let me look into that") begins streaming from Haiku. Within 1,000 ms total, Sonnet's full response is available. The latency logger captures Stage 3. Prompt caching is confirmed active (second turn in the same session is measurably faster than the first).

This is where VoiceBuddy starts sounding like a product. The dual-layer strategy is the key architectural move here — Haiku's filler buys the perceived time that makes Sonnet's slightly higher latency invisible.

Tasks:
1. Wire the Flux EndOfTurn transcript into the Anthropic API. Start with Sonnet only (no filler yet) to establish a Stage 3 baseline. Log TTFT.
2. Build the system prompt: business context (the fictitious HVAC company), conversation rules (short sentences, conversational tone, never lecture), persona. This prompt stays constant across a session — it is the candidate for prompt caching.
3. Add Haiku as Layer 1. On PROCESSING state entry, fire Haiku in parallel with Sonnet. Haiku's job: generate a 10–20 token filler only. Sonnet's job: generate the full answer.
4. Update the state machine: PROCESSING → FILLER_RESPONSE fires when Haiku returns and LLM (Sonnet) has not. FILLER_RESPONSE → BOT_SPEAKING fires when Sonnet's response is ready.
5. Enable prompt caching on the Anthropic API. Confirm it is working by comparing TTFT on the first turn vs the second turn in the same session. The second turn should be noticeably faster.
6. Measure the full Stage 3 latency (end-of-speech → LLM first token) against the budget.

### Phase 6 — Add TTS + Sentence Streaming
**Days 9–12**

> **Exit Gate:** Speak a question. Hear the filler spoken aloud within 800 ms. Hear the full Sonnet response spoken aloud, sentence by sentence, streaming as it generates. Total end-to-end latency (first audio byte out) is under 1,000 ms at p75. The latched voice is the cloned receptionist voice from Phase 1.

This is the first time VoiceBuddy sounds like itself. The sentence-streaming architecture is critical: do not wait for the full LLM response before sending to TTS. Each complete sentence goes as it arrives. Split at punctuation boundaries only — mid-sentence splits produce choppy audio.

Tasks:
1. Integrate Cartesia Sonic 3 via streaming WebSocket. Send text, receive audio bytes in real time.
2. Implement the sentence splitter: buffer the LLM stream, detect sentence boundaries (period, question mark, exclamation), flush each sentence to Cartesia as it completes. Do not flush mid-sentence.
3. Play the TTS audio in the browser as it streams back from the server. Do not wait for the full sentence to finish rendering — start playback on the first audio byte.
4. Wire the filler path: Haiku's filler text goes through the same TTS pipeline. It should be the first thing the caller hears.
5. Use the cloned receptionist voice (submitted in Phase 1). Confirm it has finished processing. If not, use Cartesia's default voice for now and swap in the clone once it is ready.
6. Update the latency logger: capture Stage 4 (TTS first audio byte) and Stage 5 (network + playback). Measure the full end-to-end pipeline against the < 1,000 ms target.
7. Run 10 full conversations end-to-end. Listen. Does it sound like a person? Note anything that feels off — pacing, phrasing, unnatural pauses — and feed it back into the system prompt or sentence-splitting logic.

### Phase 7 — Barge-In, Edge Cases, Final Evaluation
**Days 12–14**

> **Exit Gate:** All five POC success criteria pass (see Section 5). If any one fails, the POC does not move to production in its current form.

This is where VoiceBuddy gets stress-tested. Barge-in is the hardest problem in voice AI. The edge-case battery is designed to find every failure mode before a real caller does.

Tasks:
1. Implement barge-in detection: the three-layer stack is (a) WebRTC AEC (already active from Phase 3), (b) Silero VAD running continuously on the server — even while TTS is playing, (c) Flux StartOfTurn as the confirmation signal. VAD hit alone does not trigger barge-in. StartOfTurn does.
2. Implement barge-in action: on detection, stop TTS playback immediately (mid-sentence), cancel the in-flight LLM call if it has not finished, clear the TTS audio buffer, transition to USER_SPEAKING. The new utterance is a fresh turn with full conversation context.
3. Tune VAD sensitivity. Too high = misses real interruptions. Too low = background noise triggers false barge-ins. Test with scripted interruptions until < 100 ms TTS cutoff is consistent.
4. Implement the silence policy from Section 3.3. The "Are you still there?" and graceful-close paths.
5. Run the edge-case battery: angry caller tone, background noise, vague or off-topic questions, complete silence, gibberish, multiple speakers talking over each other, timeouts at every stage. Log every failure. Fix the critical ones (crashes, hangs) before evaluation. Document the rest.
6. Run the blind listening test: record 20+ full conversations. Have evaluators listen without being told it is AI. Target: > 40% unsure whether they spoke to a human or AI in the first 30 seconds.
7. Compile the final evaluation: latency data (p50, p75, p95 across all stages), barge-in reliability stats, on-task rate from the 50 scripted test calls, blind test results, edge-case failure log, and cost-per-minute actuals vs estimates.

---

## 5. POC Success Criteria (Exit Gates for Production)

If any one of these fails, VoiceBuddy does not move to production.

| Hypothesis | Pass Threshold | How to Measure |
|---|---|---|
| End-to-end latency is perceptibly fast | < 1,000 ms at p75 | Timestamp every stage; log total |
| Barge-in works reliably | < 100 ms TTS cutoff after speech detected | Scripted interruption tests |
| Conversation stays on-task | < 5% off-topic derailment across 50 test calls | Scripted scenario replay |
| Blind listening test fools listeners | > 40% of listeners unsure if human or AI in first 30 s | Record calls; blind evaluation |
| System degrades gracefully | Zero crashes across 100 edge-case scenarios | Noise, silence, gibberish, timeouts |

---

## 6. Key Implementation Notes for Claude Code

These are the details that matter when you sit down to write the code. They are not obvious from the architecture alone.

**Sentence splitting is the single most fragile piece of code in the pipeline.** Mid-sentence TTS splits produce choppy audio that immediately signals "bot" to the listener. Split at `.`, `?`, `!` only. Do not split on abbreviations like "Dr." or "U.S." Buffer short fragments (under ~15 words) and append them to the next sentence rather than flushing alone.

**VAD must run on the server, not the client.** The client is dumb audio I/O. All detection logic lives server-side.

**Prompt caching requires the system prompt to be structurally identical across calls in the same session.** Do not dynamically inject anything into the system prompt that changes turn-to-turn. Business context, rules, and persona go in the system prompt (cached). The conversation history and the current user message go in the user/assistant turns (not cached).

**The Haiku filler and the Sonnet response are fired in parallel, not sequentially.** The moment PROCESSING state is entered, both API calls go out simultaneously. Haiku's result gates FILLER_RESPONSE. Sonnet's result gates BOT_SPEAKING (after the filler finishes playing, or immediately if the filler already played).

**Eager End-of-Turn is off by default but the architecture must support it as a config toggle.** The Flux connection string has an `eager_eot_threshold` parameter. Leave it unset for the POC baseline. When you want to A/B test it later, it is a one-line config change — no code rewrite.

**Redis TTL should be set to 10 minutes.** Typical call is 3–5 minutes. 10 minutes gives buffer for slow disconnects and post-call logging without leaving stale sessions in memory.

**ElevenLabs Flash 2.5 is the TTS fallback.** If Cartesia returns an error or times out, route that sentence to ElevenLabs instead. The latency difference (40–90 ms vs 75–100 ms) is acceptable as a fallback. Do not silently drop audio.
