# NavisAI Web

React + TypeScript + Tailwind v4 + Framer Motion + Recharts front end for the NavisAI pipeline.

```bash
# terminal 1 — API over the sdoc_* pipeline (repo root)
venv/Scripts/python -m pip install fastapi "uvicorn[standard]"
venv/Scripts/python -m uvicorn api.main:app --port 8000

# terminal 2
cd web && npm install && npm run dev      # http://localhost:5173
```

If the API is down the UI falls back to `src/data/snapshot.json`
(regenerate with `python -m api.export_snapshot` from the repo root).
Ctrl+K opens the command palette.

## Import a folder

`Import folder` (top bar or Ctrl+K) accepts a bundle (`inbox/*.json` + `attachments/`) and/or `.eml` files with embedded attachments.
Each import is a separate dataset (switch from the top bar), saved under `datasets/` and processed with the full pipeline; SI/BL attachments are recognised by content.
Limits: 3000 files, 250 MB; types json, eml, txt, pdf, docx, xlsx.
