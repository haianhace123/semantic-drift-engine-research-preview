# Public source publication review

This checklist is for a source-code release of Semantic Drift Engine v0.1.0. It does not authorize running a hosted service for external users.

## License and ownership

The source release includes an MIT `LICENSE` with the named copyright holder. The repository owner must confirm that they own or have permission to publish all original source and documentation under this license. Check contributions, copied reference code, externally sourced fixtures and assets. The MIT license for project-owned code does not replace the licenses of installed packages or rights governing third-party marks.

## Image and trademark inventory

Review these seven tracked assets and document their provenance and usage rights before public publication:

| Path | Required review |
| --- | --- |
| `src/static/assets/auth/github.svg` | GitHub trademark and sign-in branding requirements |
| `src/static/assets/auth/google.svg` | Google trademark and sign-in branding requirements |
| `src/static/assets/brand/logo.svg` | Ownership and originality of SDE artwork |
| `src/static/assets/brand/favicon.ico` | Ownership and originality of SDE artwork |
| `src/static/assets/brand/favicon-dark.ico` | Ownership and originality of SDE artwork |
| `src/static/assets/brand/favicon-light.ico` | Ownership and originality of SDE artwork |
| `src/static/assets/brand/favicon-circle.ico` | Ownership and originality of SDE artwork |

A file named after a company is not proof that its use is licensed, and absence of an embedded font file does not establish rights to all UI assets. Audit source-generated assets, package attribution and UI references before approving publication. The report identifies review targets; it does not certify trademark compliance.

## Sensitive data and tracked files

Verify that the public release tree includes no `.git` history, real `.env`, credentials, customer material, database dumps, local snapshots, private signing keys or unapproved attachments. Automated signature scans are useful but not exhaustive. The excluded external PGVector smoke example contained a string matching a credential shape in the private committed source. It and dependent files are absent from this public candidate. If that token ever represented an active credential, rotate it independently of source exclusion.

Verify references in public documentation and examples. Do not import uncommitted files from the private working tree to make publication pass. Publish from the reviewed staging directory, not from the original private repository.

## Legal scope

The `src/static/terms.html` and `src/static/privacy.html` pages are templates intended for self-hosted deployments. They do not identify the operator of a specific service, specify retention periods or supply an effective public policy. Do not enable public account registration under these templates. A self-hosted operator must provide actual identity, contacts, legal bases, retention and data-transfer disclosures and obtain independent review before onboarding users.

## Acceptance and communication

The clean-install runtime passed 50 migrations and seven HTTP cases. Full browser E2E, email and AI provider flows, public ingress and performance remain unverified. Do not describe the release as production certified or claim complete temporal dependency coverage.

Before changing GitHub visibility to public, the publisher must personally approve the MIT license, source and artwork rights, excluded-file decisions and publicly visible documentation. Configure private vulnerability reporting in the new GitHub repository if available. A new repository with a clean initial history is preferred to making the private development repository public. Only the release tree should be committed. Do not push production configuration, local logs, receipt files or reports containing machine-local paths.
