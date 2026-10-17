# Semantic detector evidence safety

## Scope

The production drift detector is `src/detector/change_detector.py`. The production
LLM comparison is `src/diff_engine/deepseek_diff.py`. There is no parallel Gemini
semantic-diff engine. Gemini remains available for embeddings, separately from
semantic classification.

## Detector contract

- A different snapshot hash triggers inspection of the underlying snapshot
  content. String similarity may be recorded as evidence, but cannot classify
  any change as non-material or low risk.
- Changed regions preserve surrounding context (240 characters on either
  side). Adjacent windows are merged; distant windows retain their boundaries
  and ordering in the model request.
- All non-equal diff regions are included. There is no similarity-based
  filtering and no hidden per-region clipping.
- If the assembled evidence exceeds `DEEPSEEK_MAX_INPUT_CHARS` on either
  side, comparison stops before a provider request. The existing detector
  failure path records `analysis_status="failed"` with zero confidence rather
  than persisting a completed low-risk result based on partial evidence.
- If full text is identical despite different snapshot metadata, no model
  call is needed; the detector can produce an empty semantic change result.

## Remaining limits

This batch closes two false-negative mechanisms, not the entire semantic
accuracy problem. The context window may still be too short for cross-section
references or distant negation; an LLM may return an incorrect valid JSON
classification. Provider budget overflow creates a failed event rather than
silently classifying a partial document, but failed-event replay/recovery is
not part of this batch. An operator should treat failed analysis as unresolved,
not as evidence of safety.

The controlled embedding sensitivity probe does not validate these cases.
Before production calibration, use a larger reviewed dataset with long-policy
negations, numbers, permissions, paraphrases, cross-section references, and
many simultaneous changes. Test persisted event status and downstream risk
behavior, not just model text outputs.

Risk v2/v3 must also require completed semantic analysis before accepting vector
metrics. The worker filters incomplete events during discovery and checks again
before persistence; Risk v2 input normalization and Risk v3 requalification
apply the same gate. A completed embedding distance is not a completed semantic
change classification.
