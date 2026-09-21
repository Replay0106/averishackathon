# Supabase persistence

On Vercel the file system is ephemeral: anything written to disk disappears between requests and cold starts.
With Supabase configured, NavisAI keeps its state in a Supabase project instead, so documents, decisions and the
audit trail last across refreshes, sessions and deployments. Without the two environment variables the app behaves
as before and uses local files (development, tests).

## What is stored where

| Data | Where | Notes |
|---|---|---|
| Imported folders (emails, attachments) | Storage bucket `navis-documents`: `<dataset>/packs/pack-0001.zip` | The normalised dataset as a few zip objects; a dataset row in `navis_datasets` |
| Emails added later (simulated or live Gmail) | Bucket: `<dataset>/extra/<email>/...` | Individual objects, restored into the Gmail dataset and the demo inbox |
| Audit ledger | Table `navis_ledger` | One hash chain shared by all server instances; a database trigger makes it append-only |
| Amendment outbox | Table `navis_amendments` | Unique on the exact differences, so an amendment is never sent twice |
| Reviewer decisions (Override, Corrected document received) | Table `navis_decisions` | Previously memory only |
| Gemini vision reads and call log | Tables `navis_vision_cache`, `navis_vision_calls` | Scanned pages are not re-read or re-billed after a cold start |
| Gmail watcher state (counters, processed ids) | Table `navis_kv` | The Gmail login files (`credentials.json`, `token.json`) are deliberately **not** stored |

All tables have row-level security enabled with no policies, so the public anon key can access nothing. The API uses
the service-role key, which must only ever exist on the server (Vercel environment variables, a local `.env`).
The bucket is private.

## Setup

1. **Create the tables and the bucket.** Run `supabase/migrations/20260922000000_navis_persistence.sql` once
   (Supabase dashboard, SQL editor; or through the Supabase MCP server).
2. **Set two environment variables** (never commit them):
   - `SUPABASE_URL`, for example `https://dfmrmbyifqcgwzjntngw.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY`, from Project Settings, API
   - optional: `SUPABASE_BUCKET` (default `navis-documents`)

   Locally, put them in `.env` (see `.env.example`). On Vercel: Project, Settings, Environment Variables, or
   `npx vercel env add SUPABASE_URL production`.
3. **Check the connection:** `python -m sdoc_store --selftest` writes and reads a probe row and object in every
   table and the bucket, and prints one line per check.
4. **Redeploy** so the new variables take effect. `GET /api/health` then reports
   `"storage": {"backend": "supabase", "persistent": true, "ok": true}`.

If the variables are set but the migration has not been applied, the API refuses to start and says so, instead of
silently losing data.

## How a folder import works with Supabase

1. The browser zips the picked folder into parts under 45 MB and uploads each part **directly to Supabase Storage**
   through a signed URL from `POST /api/datasets/uploads`. (Vercel functions reject request bodies above about
   4.5 MB, so the files cannot go through the API.)
2. `POST /api/datasets/<id>/finalize` reads the parts, normalises them exactly like a local import, stores the
   dataset as zip packs and a dataset row, and deletes the raw parts.
3. After a restart or cold start the dataset is listed from the table and copied from the bucket into a local cache
   the first time it is opened.

Without Supabase the old `POST /api/datasets/import` upload is used.

## Limits

- The local disk (`/tmp` on Vercel) is still used as a **cache** of stored datasets. Nothing is lost when it is wiped;
  it is rebuilt from the bucket.
- Finalising an upload runs inside one function call (60 seconds on the current Vercel setting), so very large
  folders may need a higher `maxDuration` or a higher plan.
- Pipeline results (`run_email`) are recomputed per server instance; only the inputs and decisions are persisted.
- The Trust Gateway's sender trust scores are derived by replaying the demo inbox at startup, so they are rebuilt
  rather than stored.
- A background Gmail polling thread cannot run on Vercel (an instance is frozen after it responds); polling needs a
  long-running host. Simulated Gmail emails work because they are stored when created.
- The Supabase Storage default per-object limit is 50 MB; parts are kept under it.
- The HTTP layer talks to Supabase's REST and Storage APIs; it is unit-tested against recorded request shapes and an
  in-memory backend, and should be confirmed against the real project with `python -m sdoc_store --selftest`.
