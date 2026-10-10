# Voice AI Patient Registration

Call a real U.S. phone number, talk to "Ava", and register as a new patient. The record is saved to Postgres and
is available through a REST API and a small dashboard. Calling back with the same phone number is recognised.

| | |
|---|---|
| **Phone number** | `+1 XXX XXX XXXX`  <- fill in after Vapi setup |
| **API base URL** | `https://YOUR-APP.vercel.app` |
| **Dashboard** | `https://YOUR-APP.vercel.app/` |
| **API docs** | `https://YOUR-APP.vercel.app/docs` |

## Architecture

```
 Caller --PSTN--> Vapi (telephony + STT + LLM + TTS) --tool calls (HTTPS)--> FastAPI /vapi/webhook
                                                                                   |
                    Browser/curl --> FastAPI REST /patients  -----------------> app/db.py --> Supabase Postgres
```

| Concern | Where | Notes |
|---|---|---|
| Telephony, STT, TTS, turn-taking, interruptions | Vapi (managed) | Free inbound US number; AssemblyAI for STT |
| Conversation logic | `prompts/system_prompt.md` | Prompt-driven; no state machine to maintain |
| Voice <-> data bridge | `app/vapi.py` | 4 tools, never raise, always return a speakable result |
| Validation rules | `app/validators.py` | **One** rule set used by REST *and* the voice `validate_field` tool |
| REST API | `app/main.py`, `app/schemas.py` | Envelope `{data, error}`, 200/201/400/404/409/422/500 |
| Data layer (all SQL) | `app/db.py` | The only write path; voice and REST share it |
| Schema | `db/schema.sql` | Enum, CHECK constraints, partial unique index, `updated_at` trigger, soft delete |

### Why these choices
- **Vapi**: telephony/STT/TTS are commodity; the assessment is about integration, prompt and error handling.
- **FastAPI + pydantic**: typed validation and auto docs with very little code.
- **Postgres (Supabase)**: real constraints, survives restarts, free tier. SQLite was rejected because serverless
  hosts have ephemeral disks.
- **Validation in two places on purpose**: the voice agent calls `validate_field` right after each answer so it can
  re-prompt for *that exact field*; the API re-validates everything because it never trusts its callers.
- **Unique active phone number** (partial unique index) powers duplicate detection and makes the check race-safe.

### Voice agent behaviour (see `prompts/system_prompt.md`, commented)
Collects required fields in any order -> offers optional fields (email, insurance, emergency contact, language) ->
reads everything back -> saves only after an explicit "yes" -> "You're all set, {first name}." -> ends the call.
Also: corrections, "start over", unclear audio, medical-emergency redirect, Spanish switch, returning-caller
detection (asks to update instead; verifies date of birth before changing anything).

### Edge cases
| Situation | Behaviour |
|---|---|
| Invalid DOB / phone / state / ZIP / email | `validate_field` returns the specific problem; agent re-asks only that item. API returns 422 with `fields[]` |
| DB write fails | `register_patient` returns `system_error` + a script; caller is told nothing was saved and to call back. Never silence, never false success |
| Call drops mid-call | Nothing is saved until the confirmed `register_patient`; no partial records. `end-of-call-report` still stores the transcript |
| Caller wants to start over | Prompt instructs the agent to confirm, discard collected data and restart |
| Phone already registered | Agent offers to update; REST returns 409 `duplicate_phone` |
| Webhook called without secret | 401 |

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest httpx
cp .env.example .env   # fill DATABASE_URL and VAPI_SECRET, then: export $(cat .env | xargs)
psql "$DATABASE_URL" -f db/schema.sql      # or run db/schema.sql in the Supabase SQL editor
uvicorn app.main:app --reload
pytest                                     # 20 tests, no database needed (in-memory fake)
```

## Environment variables
| Name | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (Supabase *transaction pooler*, port 6543) |
| `VAPI_SECRET` | Shared secret; Vapi sends it as `X-Vapi-Secret`. If unset, the webhook is open (dev only) |

No keys are in the source. Vapi/AssemblyAI keys live only in the Vapi dashboard.

## Deploy (Vercel) and connect Vapi
1. Push this repo to GitHub, import it in Vercel, set `DATABASE_URL` and `VAPI_SECRET`, deploy.
2. Check `https://YOUR-APP.vercel.app/health` and `/patients` (the two seed patients should appear).
3. Vapi dashboard -> Provider Keys -> add AssemblyAI. Create the 4 tools from `vapi/tools.json`
   (replace the URL and secret).
4. Create an assistant (`vapi/assistant.example.json` is a reference): paste the prompt, attach the tools, add the
   **End Call** tool, set transcriber = AssemblyAI, choose a model and voice, set Server URL + secret and enable
   `tool-calls` and `end-of-call-report`.
5. Phone Numbers -> Create -> Free Vapi number -> attach the assistant. Call it.

## Logging
Final saved payload (`registration_complete`), updates, call-end reason, summary and transcript are logged to
stdout (Vercel logs) and the transcript/summary is stored in `call_logs`, linked to the patient
(`GET /patients/{id}/calls`).

## Known limitations / trade-offs
- Demo data only; no HIPAA controls (per the brief). The API and dashboard are **unauthenticated** so reviewers can
  use them; production would need auth, audit logs and encryption of PII at rest.
- One patient per phone number (families sharing a number can't both register).
- Vercel serverless: a cold start can add ~1 s to the first tool call of a call.
- Spanish switch relies on the chosen STT/TTS supporting Spanish; validation messages the agent paraphrases are
  English-sourced.
- Dates of birth are accepted as MM/DD/YYYY or ISO; two-digit years are rejected.
- `<` and `>` are stripped from free-text fields as basic sanitisation; SQL is fully parameterised.

## Next steps
Appointment scheduling after registration (mock slots tool), auth on the REST API, rate limiting, per-call
latency metrics, an integration test against a real Postgres in CI, Spanish validation messages.
