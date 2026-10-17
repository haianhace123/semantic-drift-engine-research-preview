# Open-source security notes

This is a source-only Research Preview, not an approved hosted service.

The public snapshot deliberately omits the former `.github/workflows/ci.yml` file. A Gitleaks scan identified a generic-credential-shaped match in that workflow. Rather than alter an unverified workflow or add a scanner exception, the release excludes it entirely. Recreate CI from reviewed configuration before relying on automation.

The Product 10 document was edited to express tenant authorization requirements without an ambiguity that matched the same broad Gitleaks rule. This wording change does not assert that external integration acceptance has been completed.

The original development repository history is not part of this release. It contains older findings that require review; any actual credentials there must be revoked or rotated independently. An automated PASS on the publication snapshot does not certify the historical repository, data privacy, or legal rights to distribute third-party assets.

The MIT license applies only where the publisher has the rights to grant it. See `LICENSE`, `BRAND_NOTICE.md`, and `docs/asset_provenance_review.md`. The sample Terms and Privacy pages are not suitable for operating a public hosted service without operator-specific review.
