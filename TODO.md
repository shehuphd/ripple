# Ripple: TODO

## Before submission (blocking)

- [ ] **Strip every non-Google LLM provider.** The hackathon rules bar non-Google AI models, agent frameworks, and AI APIs. OpenAI, Anthropic, and DeepSeek adapters exist for development only, because DeepSeek is far cheaper to iterate against. Before submitting, delete `ripple/llm/openai_compatible.py` (which serves both OpenAI and DeepSeek) and `ripple/llm/anthropic_provider.py`, remove their three entries from `PROVIDERS` in `ripple/llm/__init__.py`, drop the `openai` and `anthropic` extras from `pyproject.toml`, and delete the provider tests in `tests/test_llm.py` that reference them. Then grep the tree for `openai`, `anthropic`, `deepseek`, and `OPENAI_API_KEY`-style names and confirm nothing survives in code, README, USAGE, CHANGELOG, or the demo corpus. Gemini stays as the only provider.
- [ ] Flip the repository to public and confirm GitHub's About section shows the AGPL-3.0 licence
- [ ] Deploy to Replit Starter Autoscale on `replit.app` and record the URL
- [ ] Record the three-minute demo video against the beat list in the PRD
- [ ] Apply for the Google Cloud credit and set spend caps before any bulk extraction
- [ ] Measure Replit Starter cloud-credit usage on a deployed run

## Build order

- [x] Import adapters for Fountain, Final Draft XML, PDF, and plain text
- [x] Demo corpus, three scripts in four formats each
- [x] TraceAct instrumentation on `script.import`
- [x] SQLAlchemy models and persistence
- [x] LLM provider adapters behind one contract
- [ ] Scene-level graph extraction, resumable and browser-driven
- [ ] Deterministic diff engine
- [ ] Continuity retrieval and the ripple preview pipeline
- [ ] Change-set service with atomic acceptance and latest-only undo
- [ ] Natural-language query grounded in accepted assertions
- [ ] Web application and the local graph view

## Decisions still open

- [ ] Graph renderer: 2D or 3D, per PRD section 17
- [ ] Whether `pdftoppm` and `tesseract` can be installed on Replit Autoscale through Nix; if not, scanned PDFs stay rejected in production
- [ ] Whether intercut sub-headings should collapse into their parent numbered scene rather than becoming separate scenes (see `demo-scripts/03-seven-minutes/dependencies.md`)

## Corpus

- [ ] Seven more demo screenplays to reach the bundled ten
- [ ] Two public-domain play conversions so the corpus is not entirely synthetic
- [ ] A gitignored fixture set of downloaded studio PDFs for adversarial parser tests, never redistributed
