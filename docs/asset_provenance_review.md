# Asset provenance and third-party marks

This is a publication review record for the v0.1.0 research-preview source release. It is not a statement that any trademark or copyright issue has been cleared. A source-code release does not authorize a public hosted service.

## Project artwork

The SDE brand image files are `src/static/assets/brand/logo.svg` and the four favicon variants. The four ICO files have multiple resolutions. The displayed favicon motif and the current SVG logo are visually different; the project owner should decide whether that mismatch is acceptable for the first public preview.

`logo.svg` contains an embedded C2PA provenance manifest that identifies Claude as a tool involved in creating or modifying the file. The manifest is preserved without alteration. Its presence does not establish exclusive copyright, identify a human owner or settle commercial redistribution rights. The owner must confirm that the artwork may be distributed and, if appropriate, licensed or reserved separately from the project code. No assertion of exclusive ownership is made here.

## Identity provider marks

The login and signup pages use separate SVG marks for Google and GitHub. Their appearance in the repository is for identifying external sign-in integrations; they do not imply endorsement by either provider. The repository's MIT software license does not grant rights to either provider's marks.

The original UI labeled the buttons simply `Google` and `GitHub`. This review updates the action labels to `Continue with Google` and `Continue with GitHub`, including the labels restored by `resetOAuthButtons()`, and gives the controls sufficient width for the full call to action. The color Google 'G' remains unchanged and is shown on a white backing. This limited presentation correction is not a certification that the supplied Google SVG matches Google's pre-approved asset. The owner should compare the mark and full button appearance against the provider's current guidelines or replace it with the provider's official assets before enabling OAuth for public users.

Google Sign in branding: https://developers.google.com/identity/branding-guidelines

GitHub logo policy: https://docs.github.com/en/site-policy/other-site-policies/github-logo-policy

GitHub brand toolkit: https://brand.github.com/foundations/logo

The GitHub icon is rendered as white artwork on the SDE dark UI; its exact origin has not been established. Verify use under GitHub's policy and use an official permitted asset where needed.

## Software license decision

The draft `LICENSE` contains the standard MIT terms and a 2026 named copyright holder. It does not prove that the named person has authority to license every included file. The owner must confirm source and documentation ownership, permission for any contributed or adapted material, and the intended treatment of original project artwork. Third-party dependencies retain their own terms. If a reservation of project trademarks and brand artwork is desired, document the scope explicitly and obtain appropriate review before the initial public commit.

## Sign-off required before publication

- Confirm that the person named in `LICENSE` is authorized to license the original SDE source and documentation under MIT.
- Confirm the provenance and publication rights of the SDE SVG, favicons and any other bundled artwork.
- Verify the exact Google and GitHub icon provenance, allowed usage and button appearance under their current guidelines.
- Recheck the final source tree for personal information and credentials; pattern-based scans cannot prove their absence.
- Keep hosted public registration disabled until an operator-specific Terms and Privacy notice and the relevant user flows have been reviewed.

The updated HTML/CSS/JS are a release-source change made after RC2 runtime acceptance. Existing RC2 build and runtime receipts establish the previous image, not a rebuilt image from this modified source. Run targeted UI tests and, if an image release is planned, build and verify an image from the final source separately.
