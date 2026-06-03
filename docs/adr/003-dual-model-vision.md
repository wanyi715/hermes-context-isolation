# ADR-003: Dual Model Strategy (Vision vs Text)

**Date:** 2026-06-03
**Status:** Accepted

## Context

The main dialogue model is `mimo-v2.5-pro`, which excels at reasoning and long-context tasks but **does not support image input**. Users occasionally send images (screenshots, photos, HEIF files from iPhones) in the Web Chat.

## Decision

Use a dual-model architecture:

| Role | Model | Capabilities |
|------|-------|-------------|
| Main dialogue | mimo-v2.5-pro | Text reasoning, tool calling, long context |
| Vision | mimo-v2.5 | Image understanding, audio transcription |

**Flow:**
```
User sends image → mimo-v2.5 describes image → text description injected into context → mimo-v2.5-pro responds
```

This is a **describe-then-inject** pattern: the Vision model produces a text description, which becomes part of the main model's context.

**HEIF handling:** iPhone photos use HEIF format, which the MiMo API doesn't support. Added `pillow_heif` backend conversion to JPEG before sending to the API.

## Alternatives Considered

1. **Single multimodal model** — Would require switching from mimo-v2.5-pro to a model that supports both vision and long-context reasoning. Current MiMo API doesn't offer such a model.
2. **External Vision API (GPT-4V, Claude)** — Adds another API dependency and cost. Same API key works for both mimo-v2.5 and mimo-v2.5-pro.
3. **Client-side image description** — Browser can't run vision models. Not feasible.

## Consequences

**Positive:**
- Same API key and base_url for both models
- Vision model also supports audio transcription (useful for content production workflows)
- Main model's reasoning quality unaffected
- HEIF conversion handles iPhone photos seamlessly

**Negative:**
- Two API calls per image message (slight latency increase)
- Vision description quality depends on mimo-v2.5, not the stronger pro model
- Config must maintain correct api_key for both models (key mismatch causes silent failures)
