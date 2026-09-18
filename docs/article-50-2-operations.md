# Article 50(2) compliance record

Hivegent is developed in-house and put into service under its own name, which makes the operator a provider under Article 50(2) of Regulation (EU) 2024/1689.
The system generates free-form text that natural persons read, so the marking and detection obligation applies.
None of the three exceptions in Article 50(2) fit: the system generates rather than performs standard editing, and the industrial and business-to-business carve-out in the Commission guidelines needs strictly technical output, which a retrieval assistant does not produce.

This record follows the Commission's Code of Practice as a reference for the state of the art.
Hivegent is not a signatory.

## What is marked

Free-form text longer than 200 tokens is marked at inference time by vLLM's `gumbel` watermark.
A single watermark layer is what the Code considers sufficient for free-form text, since such text carries no metadata.
Shorter text cannot be watermarked with even basic reliability and is not marked.

Documents Hivegent writes into the workspace and conversation exports carry the watermark but no second, metadata-based layer.
The Code asks for signed metadata alongside the watermark for text in a format that can hold it.
This gap is open and is reviewed together with the detector interoperability work due by 2 February 2027.

Structured output consumed only by machines is out of scope under the Commission guidelines and is not marked.

## Runtime configuration

Watermarking is a property of the inference server, not of Hivegent.
Enable it in the vLLM configuration:

```bash
vllm serve <model> --watermark-config '{"algorithm":"gumbel","key":<secret-64-bit-integer>}'
```

Hivegent sets no sampling parameters, so the server's configuration decides them.
Greedy decoding bypasses a Gumbel watermark, so the server must be configured for stochastic sampling, and request-level opt-out must not be exposed.
Keep vLLM private to Hivegent and llmhop.

Do not enable watermarking before a stable vLLM release carries upstream commit `ea40bb9e905f8d552281dd1ec074f91865a0a242` or equivalent support.

Configure Hivegent with:

```toml
# At least 32 random characters, e.g. `openssl rand -base64 32`. Required once
# transparency is enabled, since the report signing key is derived from it.
secret_key = "..."

[transparency]
enabled = true
detector_url = "http://127.0.0.1:18001/detect"
detector_api_key = ""
report_issuer = "https://hivegent.example.eu"
contact_email = "responsible-operator@example.eu"
```

`detector_url` is vLLM's reference detector, which takes `{"text": "..."}` and answers with `score`, `p_value`, `num_scored_tokens`, and `is_watermarked`.
Hivegent speaks that contract directly so that no Hivegent-specific detection format exists.

## Keys

Generate a random unsigned 64-bit watermark key at deployment time and keep it outside the Nix store, readable only by vLLM.
Keep the active key on the first line and retained keys below it, rotate by prepending a new key and restarting vLLM, and record the date.
Retained keys stay in the detector so that previously marked text remains detectable.
Never publish watermark keys.

Hivegent derives its Ed25519 report-signing key from `secret_key` (`HIVEGENT_SECRET_KEY`), so the signing key itself is never stored and only its JWKS is published.
Set `secret_key` to at least 32 random characters, for example `openssl rand -base64 32`, and keep it stable across deployments: changing it rotates the signing key, and reports issued under the old key stop verifying.
Rotating it deliberately is how those reports are revoked.

## Detection

`POST /api/transparency/detect` answers for signed-in users, which covers the persons exposed to the content.
`GET /api/transparency/jwks` publishes the report verification key behind the same authentication.
It carries only a public key, so exposing it would be harmless, but a report only ever reaches someone who already has access, and an unauthenticated route is attack surface bought for nobody.
Access is free and unlimited on request through the contact address above for competent authorities, regulators, law enforcement, media, fact-checkers, researchers, and civil society organisations.
Granting that access means granting a sign-in, which covers both endpoints at once.

Submitted text is processed in memory only.
It is never logged, retained, used for analytics or training, or placed in a signed report.
Scores and p-values are not returned, since they are the feedback signal an attacker needs to strip or forge the watermark.

## Testing

Test before putting a configuration into service and again after any change to vLLM, the model, the tokenizer, the sampler, or the watermark settings.

Measure false positives and false negatives on marked, lightly edited marked, and unmarked samples in German and English, using samples not drawn from development.
Record the configuration, software versions, sample definitions, aggregate results, limitations, reviewer, and date.
Do not retain confidential sample content.

Act on any shortcoming found here or reported by a third party, and disable affected generation if marking stops being reliable.

## Acceptable use and ownership

Removing, forging, concealing, or circumventing the watermark is prohibited, and this prohibition is stated in the user documentation.
Hivegent does not distribute tools for circumventing markings.

Assign an owner and a backup for this record.
Keep it and the test results available for competent market surveillance authorities, and reassess before enabling image, audio, or video generation.

Sources:

- [Article 50 of Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [Code of Practice on Transparency of AI-generated Content](https://ec.europa.eu/newsroom/dae/redirection/document/129555)
- [Commission guidelines on AI-generated content transparency obligations](https://ec.europa.eu/newsroom/dae/redirection/document/131215)
