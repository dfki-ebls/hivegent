# AI transparency

Hivegent identifies itself as an AI system in the chat interface.
When watermarking is enabled, it marks generated text longer than 200 tokens under the European Commission's [Code of Practice](https://ec.europa.eu/newsroom/dae/redirection/document/129555).
Shorter text is not reliably covered.

## Verify text

Select **Verify AI text** below the chat composer and paste the text to check.
You can download a signed report containing the text hash, but not the text itself, and verify it with the key published at `/api/transparency/jwks`, which needs the same sign-in as the check.
Submitted text is not stored or logged.

A positive result means that Hivegent's watermark was found.
A negative result does not prove human authorship or exclude another AI system.
Short or edited text may be inconclusive.

Sign-in is required.
Qualified experts can request access through the operator contact shown in the verification dialog.

## Acceptable use

Do not remove, forge, conceal, or use the detector to circumvent the watermark.
Keep a visible disclosure when sharing AI-generated text where its origin matters.
