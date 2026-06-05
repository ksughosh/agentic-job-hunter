# Agentic Job Hunter

Open-source multi-agent AI job search. Upload your resume, pick roles + filters, and a pipeline of agents scrapes, scores, and ranks jobs across multiple sources.

## Features

- **Multi-agent pipeline**: resume scanner → JD refiner → scrapers → company reviewer → JD matcher
- **Multi-provider LLM**: Gemini, Groq, Ollama, MLX (Apple Silicon), LM Studio — switch at runtime
- **Auto-detect installed local models** via standard paths (`~/.lmstudio`, `~/.ollama`, `mlx_lm`)
- **Multi-user profiles** with per-profile search configs (roles, work mode, location, salary, scan depth, exclude keywords)
- **Composite scoring**: match score + work-mode fit + salary + location + seniority + contract bonus
- **Quick (~3s) vs deep (~20s) resume scan**
- **Tailored resume + cover letter generation** per job

## Setup

```bash
./install.sh   # interactive: DB choice + LLM provider selection
./run.sh       # starts Flask backend (also serves UI) on :5050
```

Open <http://localhost:5050>.

## Stack

- Python 3.9+ / Flask backend
- Vanilla JS frontend (Chart.js for visualizations)
- Supabase / local Postgres / JSON storage (configurable)
- Provider-agnostic LLM router (`agents/llm_client.py`)

## License

MIT — see [LICENSE](LICENSE).
