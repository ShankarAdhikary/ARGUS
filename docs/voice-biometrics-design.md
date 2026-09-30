# Voice biometrics (speaker identification) — design

Status: **built, switched off.** The endpoints are not registered (they answer 404) until `VOICEPRINT_ENABLED=true`, and that
must not be set until the evaluation in §7 has been run on real recordings and a calibration file exists.

## 1. Scope and premise

Speaker identification on **intercepted or recorded audio obtained under lawful authorization**. It is not a CDR feature: a CDR
is call metadata (numbers, times, durations) and contains no audio. Two operations:

* **Enroll** a recorded voice sample of a known person into the voiceprint index.
* **Match** an audio clip of an unknown speaker against the index and return ranked candidates.

Every result is an **investigative lead** and carries this label, verbatim:

> Investigative lead — requires forensic voice expert confirmation before use in proceedings.

Speaker identification is materially less reliable than fingerprints. Accuracy falls with telephone-channel audio, short
clips, background noise, language or dialect mismatch between enrolment and test, illness and disguise. ECAPA-TDNN has **no
anti-spoofing**: synthetic or replayed voices are not detected.

## 2. Authorization is part of the schema, not an option

Every enrolment and every match must carry `lawful_interception_ref`: the reference number of the authorization order.

* The endpoints reject a request without it (HTTP 422), exactly as a sensitive case rejects a request without a justification.
* It is stored on the voiceprint entry **and** written to the audit log for every enrolment and every match. Adding it later would
  leave earlier rows with no authorization on record, which cannot be repaired: hence day one.
* Validation is syntactic only (3–100 characters from a restricted set; obvious placeholders such as `NA`, `none`, `test`,
  `TBD`, `-` are refused). **The system cannot verify that an order exists.** Confirming a reference against an issuing
  authority's register would need an integration that does not exist yet; until then the reference is an accountability
  record that makes misuse attributable, not a control that prevents it. Say so to reviewers.
* Optional `case_id` / `justification` work as on the other biometric endpoints (sensitive cases require a justification).

## 3. Data model

One voiceprint entry:

| field | notes |
|---|---|
| `suspect_id` | UUID assigned at enrolment |
| `name`, `fir_id` | as supplied; `fir_id` links the voiceprint to the case record |
| `jurisdiction` | the FIR's jurisdiction (Unassigned if unknown); searches are jurisdiction-scoped, as for faces and fingerprints |
| `lawful_interception_ref` | §2 |
| `enrolled_at` | UTC timestamp |
| `duration_seconds` | length of the enrolment audio |
| `net_speech_seconds` | speech remaining after silence removal (what the embedding actually rests on) |
| `channel_quality_score` | 0–100, §5 |
| `sample_sha256` | SHA-256 of the uploaded sample, so the recording can be tied to the entry without storing it |
| `embedding` | 192-dimension ECAPA-TDNN speaker embedding, L2-normalised, float32, base64 |
| `deleted`, `deleted_at` | tombstone: deleting removes the embedding but keeps the record for audit |

**Raw audio is not stored.** Only the embedding and the sample's hash are kept, so the index holds the minimum needed to match.
A voiceprint is biometric data: treat retention, access and deletion accordingly, and take legal advice on the applicable data
protection law and on any exemptions that may or may not apply to the deploying agency.

## 4. Storage and integrity

The index is one JSON document in the MinIO bucket `voiceprint-index`, sealed with an HMAC-SHA-256 (key derived from the JWT
secret with a voiceprint-specific label, distinct from the fingerprint index's), loaded only if the seal verifies. Anyone with
write access to the bucket cannot inject an entry that would later "match". Writes take a shared Redis lock, and workers keep in
step by the object's ETag: the same design as the fingerprint index. No pickling: a tampered bucket cannot execute code on load.

## 5. Embedding, scoring and quality

* **Model:** SpeechBrain `spkrec-ecapa-voxceleb` (ECAPA-TDNN), CPU, offline once the weights are in the image.
* **Audio:** decoded with ffmpeg to 16 kHz mono; capped at 120 s.
* **Score:** cosine similarity of L2-normalised embeddings (exact, brute force with numpy: adequate to well over 100,000
  voiceprints; FAISS is not needed at this size).
* **`channel_quality_score`:** a heuristic from net speech duration, an energy-based SNR estimate, clipping and effective
  bandwidth (telephone audio is flagged narrowband). It is **not** a standard measure and is uncalibrated. The only hard gate is
  a minimum of net speech (`VOICEPRINT_MIN_SPEECH_SECONDS`, default 3 s); shorter audio is refused with HTTP 422.
* Enrolment and test conditions differ in real casework (direct enrolment sample, telephone test clip). §7 measures exactly that.

## 6. Threshold: none until it is measured

**No threshold is hard-coded.** ECAPA cosine scores have no universal meaning: the value that gives a 0.1% false-accept rate
depends on the channel, language and duration mix, and the model. Until a calibration file exists the service runs in
**ranking-only mode**:

* the response has `calibrated: false`, `match_found: null`, and candidates ranked by raw cosine score;
* every candidate's confidence label is `Uncalibrated: ranking only`, and the response says a score cannot be interpreted as
  a match probability.

With a valid calibration file (`VOICEPRINT_CALIBRATION_FILE`, written only by `scripts/evaluate_voiceprint.py`):

* candidates are listed only if their score reaches the threshold measured for a **0.1% false-accept rate** (conservative);
* labels: `High` at the 0.01% threshold (only if enough impostor trials existed to measure it), `Medium` at the 0.1% threshold,
  otherwise the candidate is not listed;
* the file records the model, dataset hash, speaker/trial counts, languages and channels; the service **refuses to load** a file
  that was made for a different model or that does not meet the minimum data requirements in §7 (and falls back to ranking-only).

## 7. Evaluation before anything goes live

The endpoints stay switched off until this has been run and reviewed. `scripts/evaluate_voiceprint.py` computes and enforces it.

**Data required** (the script refuses to write a calibration file below these; the minimums are constants shared with the service):

* known ground truth (which speaker is in each file);
* **at least three languages**: Hindi, English, and at least one regional language the deployment will meet;
* **both channels**: telephone-channel (8 kHz narrowband, real codecs such as G.711/AMR/GSM) and direct/wideband audio;
* at least **10 speakers in every language × channel cell** and **50 speakers overall**, at least two recordings each, recorded in
  different sessions (same-session pairs inflate accuracy);
* realistic durations, including short clips (3–10 s), because that is what intercept casework produces.

**What it measures:** genuine and impostor trial scores; EER; false-reject rate at the 1%, 0.1% and 0.01% false-accept points;
all broken down by language, channel, enrolment→test channel pairing (direct→phone is the hard case), duration band and
gender, so a good overall number cannot hide a bad subgroup.

**How much data a claim needs:** to *measure* a false-accept rate `p` you need roughly `10/p` impostor trials (10 expected errors):
1,000 for 1%, 10,000 for 0.1%, 100,000 for 0.01%. Trials that share a speaker are correlated, so effective sample size is lower
than the trial count; the report should give speaker-level bootstrap confidence intervals, and a threshold is reported as `null`
when there are too few impostor trials to support it.

**Acceptance:** agreed in advance with the forensic reviewer, on the subgroup results, not the average. Recalibrate whenever the
model, the audio pipeline or the channel mix changes.

## 8. Access and audit

* Enrol: supervisor or admin. Match: any authenticated role, jurisdiction-scoped.
* Audit entries (`voiceprint_enroll`, `voiceprint_match`) carry the lawful-interception reference, the sample hash, duration,
  quality, jurisdiction filter, whether the service was calibrated, and (for a match) the top candidate and score.
* A failed audit write fails the request: no unaudited biometric search returns a result.

## 9. Threats and known limits

| risk | handling |
|---|---|
| Injected/edited index | HMAC seal; refused on mismatch |
| Placeholder authorization references | rejected by validation; **a real-looking but false reference cannot be detected** |
| Spoofing (synthetic, replayed or converted voices) | **not handled**: no liveness/anti-spoofing; state this in any briefing |
| Score misread as a probability | ranking-only until calibrated; labels tied to measured false-accept rates; expert-confirmation label on every result |
| Bias across languages/genders/accents | measured per subgroup in §7; not assumed away |
| Very short or narrowband audio | 3 s minimum; quality score; measured per duration band and channel |

## 10. Deliberately not built

A UI (nothing should be presented to officers before the evaluation), an authorization-register integration, anti-spoofing,
diarisation (separating several speakers in one recording: an enrolment or match clip must contain one speaker), and language
identification.
