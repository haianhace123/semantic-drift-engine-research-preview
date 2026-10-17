# Deployment legal review

The repository contains reference HTML pages at `src/static/terms.html` and `src/static/privacy.html`. They describe the features and limitations of the self-hosted software. They are not complete legal notices for a public service.

A deployer must complete this review before enabling public registration or using the pages as binding policies.

## Operator identity and contact

Record the legal name and address of the party operating the deployment. Publish a functioning contact channel for account support, privacy rights and legal notices. Document whether the operator is acting as a data controller, processor or both for each data flow.

## Data inventory and retention

Review account and consent records, authentication/session events, organization metadata, raw source snapshots, monitored URLs, embeddings, risk and semantic evidence, lineage, alert deliveries, webhooks, logs and backups. Identify data owners, purposes, processing grounds, storage locations, export methods and retention schedules. Do not infer complete organization erasure from the account deactivation endpoint.

For each category, establish an actual retention period or defensible retention criterion, including backups and immutable audit records. Identify which data is transmitted to external AI providers, transactional email services and downstream databases.

## Terms and privacy approval

Before enabling public sign-up:

- Have the actual operator approve Terms of Service, effective date, dispute terms, acceptable use, support promises and the service cancellation process.
- Have the privacy lead or qualified counsel approve the privacy notice, data roles, lawful bases, rights requests, security contact, retention and transfer disclosures.
- Align the published terms version with the application's configured consent version and record the effective policy version.
- Review the age eligibility and any sensitive-data restrictions applicable to the intended deployment.
- Check the obligations of every enabled provider and each jurisdiction in which users or monitored data are located.
- Verify that actual on-screen account export, deactivation and last-administrator restrictions agree with the published descriptions.
- Confirm both pages remain available without sign-in and do not require inline scripts or externally hosted assets.

The legal pages must not be represented as final while they still say "Deployment template". No automated release gate can replace legal and operational review. This checklist is engineering documentation, not legal advice.

## Source publication versus hosting

Publishing source code on GitHub does not by itself create a hosted service or appoint the code maintainer as the operator of third-party deployments. A hosted public instance is a separate release and privacy decision requiring its own review.
