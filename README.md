# Local AI Job Workbench

Local-first desktop and web application for tracking engineering job postings, compiling single-page LaTeX CVs and DIN 5008 cover letters, and running structured LLM reviews with strict profile grounding.

All data stays local in SQLite (`data/jobs.sqlite3`) and is configured from `data/master_profile.example.json` and `data/sources.example.json`.

## Architecture

- **Backend (`backend/`)**: FastAPI REST server, SQLite WAL storage, multi-portal ATS feed scanner (Greenhouse, Lever, Ashby, Personio, Workday, SmartRecruiters, SuccessFactors), deterministic rule scoring + geo-tiering, PyWebView desktop shell (`backend/desktop.py`), and `lualatex` PDF generation + pre-flight checks.
- **Frontend (`frontend/`)**: React + TypeScript + Vite workbench with six views: Job Radar, Prioritized Shortlist (with drag-and-drop and AI head-to-head ranking), Companies, CV Studio, Cover Letter Editor (reusable paragraph organizer + pre-flight checks), and Application Pipeline (Sankey metrics + interview prep).
- **Templates (`templates/`)**: Bilingual (DE/EN) single-page LaTeX CV and DIN cover letter templates.

## Setup

### 1. Backend (Python 3.11+)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install .
```

Optional for LaTeX PDF export: ensure `lualatex` (TeX Live or MiKTeX) is on `PATH`.
Optional for AI workflows: ensure the Antigravity CLI (`agy`) is installed and signed in.

### 2. Frontend (`Node.js 20+`)

```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. Configuration (Optional)

By default, the workbench falls back to `data/master_profile.example.json` and `data/sources.example.json`.
To use your own data locally (ignored by Git):

```bash
copy data\master_profile.example.json data\master_profile.json
copy data\sources.example.json data\sources.json
```

Export directories can be overridden via `WORKBENCH_EXPORT_DIR` and `WORKBENCH_ATTACHMENTS_DIR`.

## Running

Start the desktop window (with embedded FastAPI backend):

```bash
python -m backend.desktop
```

Or run the web server directly at `http://127.0.0.1:8765`:

```bash
uvicorn backend.server:app --host 127.0.0.1 --port 8765
```
