# AI transparency

Hivegent identifies itself as an AI system in the chat interface and in the first reply of every Teams conversation.
When watermarking is enabled, it marks generated text under the European Commission's [Code of Practice](https://ec.europa.eu/newsroom/dae/redirection/document/129555).
Longer, unedited text improves detection, but no length guarantees a positive result.
With fewer than 200 scored tokens, a negative result is inconclusive, and repeated passages may count only once.
Conversation exports carry a signed statement that they contain AI-generated text.

## Verify text

Select **Verify AI text** in the user menu and paste the text.
You can download a signed report with the text hash, but not the text.
Verify the report, or the `provenance` field of an export, with the public key at `/api/transparency/jwks`.
Submitted text is not stored or logged.

A positive result means Hivegent's watermark was found.
A negative result does not prove human authorship, and short or edited text may be inconclusive.

The check requires sign-in.
Qualified experts can request access through the operator contact in the verification dialog.

## Acceptable use

Do not remove, forge, conceal, or circumvent the watermark.
Keep a visible disclosure when sharing AI-generated text where its origin matters.
