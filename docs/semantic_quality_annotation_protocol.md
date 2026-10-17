# Semantic quality annotation protocol

Status: offline intake and annotation workflow. Not an independent benchmark and not a production model-release gate.

## Product hypothesis

SDE's commercial claim is that it can prevent downstream AI systems from using materially outdated knowledge without unnecessarily blocking equivalent rewordings. Product 07, 10, and 11 have source and isolated integration acceptance for enforcement. These tests do not measure whether the semantic detector classifies unseen customer revisions correctly. The 24-case synthetic diagnostic must not be represented as independently labeled accuracy evidence.

## Evaluation authority

The sole evaluator is `scripts.evaluate_semantic_holdout` and the semantic holdout service already in this repository. `scripts.semantic_quality_intake` is an offline input-preparation utility, not another scoring engine or model authority.

Run `python -m scripts.semantic_quality_intake --help` to inspect its `prepare`, `finalize`, and `self-test` commands.

- Keep all real corpus text, blinded HTML, reviewer labels and finalized holdouts **outside the repository**, e.g. `C:\HaiAnh\sde_private_eval`. Never commit or paste customer or unreleased policy text in an issue, chat, CI report, or code review.
- Start with a single problem domain: contractual/policy revisions that affect how an AI consumer answers or acts. Include both meaningful and meaning-preserving changes, source-document identity, exact old/new revision provenance and a documented rights basis.
- Split by **source-document family before model selection**. No revisions from the same document family may be both in the tuning set and independent holdout. Keep source and policy changes time-ordered where applicable.
- Two reviewers label separately as `material`, `equivalent` or `unsure` without seeing model output or each other's responses. Resolve all disagreements or unsure cases with documented adjudication. A human must attest that the evaluation set was actually held out.
- Material means obligations, restrictions, permitted actions, factual answers, eligibility, scope, timing, quantity, exceptions, privacy or security requirements change. Equivalent means formatting, paraphrasing or restructuring preserving relevant constraints. Do not infer semantic equivalence from text similarity alone.
- Freeze the private intake and labels with SHA-256 receipt; finalize only after adjudication. The tool's provenance and reviewer independence are **self-declared**, not independently verified by software.
- Run `python -m scripts.evaluate_semantic_holdout --corpus <private-holdout>` in **plan mode**. Provider calls require separate permission to transmit document content, non-production credentials and an explicit bounded budget. Do not run `--execute` as part of this batch.
- Report absolute true/false positive and negative counts, abstentions, coverage, precision/recall on completed decisions, yield with abstentions, cost, latency and results by domain. A group-level confidence interval is preferable to a single aggregate score for a small holdout. Do not tune the detector on the independent holdout.

## Public corpus candidate (not yet ingested)

The `github/site-policy` repository has version-controlled policy documents and publishes the repository content under CC0-1.0. It provides an initial **candidate source** for policy revision pairs, not labeled ground truth. Check actual commit identity and substantive diff context; repository history may not mirror exactly the current live website. Capture licenses and URLs at acquisition time. Its public license does not automatically extend to unrelated repositories or trademark assets.

Source: https://github.com/github/site-policy

Additional datasets such as Open Terms Archive releases carry separate database licensing obligations (including ODbL in the demo release). Review those obligations before mixing, distributing or publishing a derived benchmark.

Do not extrapolate results from one provider's policy history to all domains or customer workflows. If appropriate independent reviewers and sourced text are unavailable, Product 12 remains **blocked on evaluation evidence**, not on more architecture.

## Synthetic fixture

`tests/fixtures/semantic_quality_intake_synthetic_only.json` is strictly a sample schema and smoke-test input, not an annotation dataset, benchmark, or evidence of detector quality.

## Production release boundary

A successful annotation-tool test says only that the offline workflow rejects malformed input and produces a corpus compatible with the existing holdout evaluator. It neither establishes detection accuracy nor justifies replacing the model or changing production admission behavior.
