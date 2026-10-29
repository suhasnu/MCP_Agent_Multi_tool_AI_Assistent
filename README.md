# MCP Multi-Tool AI Assistant

[![CI](https://github.com/suhasnu/MCP_Agent_Multi_tool_AI_Assistent/actions/workflows/ci.yml/badge.svg)](https://github.com/suhasnu/MCP_Agent_Multi_tool_AI_Assistent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)

An LLM agent that answers questions about German weather by **writing its own SQL** against a data warehouse it queries live and combines that with real-time conditions from a public API in a single answer.

Most Model Context Protocol demos are a chat box wired to someone else's API. This one has two halves that meet in the middle: a full ETL pipeline that turns raw weather-service archives into a queryable warehouse, and an agent that queries it. The join between them is *measured*, not assumed.

![The agent answering a live-plus-historical question](docs/demo.png)

> **Asked:** *"What is the weather in Berlin right now, and how does it compare to Berlin's average June temperature in the data?"*
> The agent calls a live weather API **and** writes SQL against the historical warehouse, then answers from both, noting that current conditions are 5.1 °C warmer than the June average recorded at the Berlin-Tempelhof station.

Every tool call is shown live in the interface, with its arguments and latency:

![The live tool-call panel](docs/trace.png)

## What it does

- **Generates SQL from natural language** against a DuckDB warehouse of German weather observations.
- **Fetches live weather worldwide** via Open-Meteo (no API key) and reasons across live + historical data in one response.
- **Shows its work** every tool call, with arguments and latency, streams to the interface as it happens.
- **Measures itself** an evaluation harness scores tool selection and SQL accuracy across graded scenarios.

## Architecture

```
BUILD PATH  (offline, scheduled via GitHub Actions)
  ┌─────────────┐   ┌──────────────────────────────┐   ┌───────────────┐
  │ DWD station │──▶│  ETL pipeline                │──▶│  quality gate │
  │  archives   │   │  ingest → bronze → silver →  │   │  (14 checks)  │
  └─────────────┘   │  gold                        │   └───────┬───────┘
                    └──────────────────────────────┘           │ pass
                                                                ▼
                                                        ┌───────────────┐
                                                        │    DuckDB     │
                                                        │   warehouse   │
                                                        └───────┬───────┘
                                                                │
  SERVE PATH  (real time)                                       │ reads
  ┌──────────┐   ┌───────────────┐   ┌──────────────┐   ┌───────▼──────────┐
  │  User /  │──▶│  FastAPI      │──▶│  MCP agent   │──▶│ analytics server │
  │ Streamlit│◀──│  gateway      │◀──│ Gemini/Groq  │   │  read-only SQL   │
  │    UI    │   │  (HTTP + SSE) │   └──────┬───────┘   └──────────────────┘
  └──────────┘   └───────────────┘          │
                                            │  ┌──────────────────┐   ┌─────────────┐
                                            └─▶│ live-weather srv │──▶│ Open-Meteo  │
                                               │   (worldwide)    │   │     API     │
                                               └──────────────────┘   └─────────────┘
```

- **Data pipeline** Deutscher Wetterdienst (DWD) station archives are ingested through a medallion architecture: `bronze` lands raw text with provenance, `silver` types and cleans it, `gold` aggregates it. A 14-check quality gate fails the build on bad data.
- **MCP servers** the agent reaches the warehouse through a custom [Model Context Protocol](https://modelcontextprotocol.io) server that enforces read-only SQL at the DuckDB engine, not by string matching. A second MCP server provides worldwide live weather.
- **Gateway** a FastAPI service owns the agent and streams tool events and the answer over Server-Sent Events. The Streamlit UI is a thin client holding no agent state.
- **Provider-agnostic** the model layer switches between Google Gemini and Groq with one environment variable.

The gateway's auto-generated OpenAPI docs, and the raw SSE stream a request produces:

![OpenAPI docs](docs/api.png)

![Server-Sent Events stream: tool, then answer, then done](docs/sse.png)

## Where this sits in the AI engineering stack

Chip Huyen's *AI Engineering* (ch. 1) splits the work into three layers: **application development**, **model development**, and **infrastructure**. Mapping this repository onto them is a useful exercise, because it shows which parts of the system are AI engineering and which are ordinary data or full-stack work wearing an AI label.

| Component | Layer | What kind of engineering it actually is |
|---|---|---|
| Streamlit UI | Application development | Full-stack. A thin client that holds no agent state. |
| FastAPI gateway (HTTP + SSE) | Application development, spilling into infrastructure | Full-stack and serving. It owns the agent and streams events, but nothing about it is AI-specific. |
| MCP servers and their tool definitions (analytics, live weather) | Application development | **AI engineering.** This is context construction: the tool inventory is the entire surface the model can see and act through, so its shape determines what the agent can do. |
| Agent prompt and tool-selection behaviour | Application development | **AI engineering.** Prompt-based adaptation, which is the lever available when you do not own the weights. |
| Provider switch between Gemini and Groq | Model development, at its boundary | **AI engineering**, but model *selection*, not model development. Nothing here is trained, finetuned, or distilled. |
| DWD ETL pipeline: ingest, bronze, silver, gold | Infrastructure | Classical data engineering. The medallion split and the Bundesland weighting decision are data modelling choices, not AI ones. |
| 14-check quality gate | Infrastructure | Classical data quality engineering. |
| DuckDB warehouse with read-only SQL enforced at the engine | Infrastructure | Data management, plus a security control placed at the layer that can actually enforce it rather than in a prompt. |
| ETag download caching, prompt-hash result caching | Infrastructure | Classical caching. |
| pytest suite (98 functions), ruff, GitHub Actions CI | Infrastructure | Classical software engineering. |
| Evaluation harness (17 graded scenarios, tool-selection and SQL execution accuracy) | Spans all three, currently weighted to application | **AI engineering.** See the note below. |

### The model development layer is deliberately empty

No model is trained, finetuned, quantized, or served from local weights in this project. Gemini and Groq are called over an API. That is not a gap to apologise for, it is the shift the book describes: the foundation model arrives ready-made, so the work moves out of modelling and training and into adaptation, context, and evaluation. The consequence is that inference cost and latency are runtime concerns here rather than one-off training costs, which is why every tool call is timed and surfaced in the interface.

### Evaluation does not belong to one layer

A wrong answer from this agent can originate in any of the three layers and looks identical from the outside. Bad generated SQL may come from the context the agent was given (application), from the model being unable to handle that query shape at all (model), or from a truncated stream, a timeout, or stale gold tables (infrastructure). End-to-end scoring alone cannot tell these apart. That is exactly what the first evaluation run demonstrated: a 33% exact-match score that turned out to be an over-strict metric plus a prompt bug rather than a model failure.

The harness currently scores tool selection and SQL execution accuracy end to end. Per-layer attribution, and an explicit written guideline defining what a correct answer is, are known gaps rather than solved problems.

## Quickstart

```bash
git clone https://github.com/suhasnu/MCP_Agent_Multi_tool_AI_Assistent.git
cd MCP_Agent_Multi_tool_AI_Assistent
python -m venv .venv
.venv\Scripts\activate            # Windows  (use: source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

copy .env.example .env            # then add a free Gemini key from aistudio.google.com

# Build the warehouse (downloads a subset of DWD stations)
python -m pipeline.run --stations 5
python -m pipeline.bronze
python -m pipeline.silver
python -m pipeline.gold
python -m pipeline.quality

# Run it (two terminals)
uvicorn gateway.main:app          # terminal 1: the agent, behind HTTP
streamlit run ui/app.py           # terminal 2: the UI
```

UI at http://localhost:8501, interactive API docs at http://localhost:8000/docs.

## The data pipeline

| Layer | What it holds | Key decision |
|---|---|---|
| bronze | Raw station rows as delivered, plus `source_file` and `ingested_at` | Everything is text, so a parsing fix re-runs silver instead of re-downloading |
| silver | Typed, cleaned readings with station metadata joined | `-999` sentinels become NULL; a LEFT JOIN keeps unmatched readings instead of dropping them |
| gold | Daily and monthly aggregates per station and per federal state | Bundesland averages are computed from silver, not from station averages, so a station with 40 readings isn't weighted like one with 700 |

Downloads are ETag-cached, so a re-run re-checks every station but downloads only what changed.

## Evaluation

`python -m evals.run` scores 17 scenarios on two metrics:

- **Tool-selection accuracy**  did the agent reach for the right tool? (~94%)
- **SQL execution accuracy**  does the generated query return the same rows as a hand-written gold query? (the Spider/BIRD result-set metric)

Scenarios are graded easy/medium/hard, and results are cached by prompt hash to respect free-tier rate limits.

The harness earned its keep. The first run scored 33% exact-match, but reading the failures showed most were the model returning a *correct* answer with an extra column, alongside a prompt bug that copied `LIMIT 50` into queries. That led to adding a containment metric and fixing the prompt exactly the kind of diagnosis the harness exists to enable. On hard, multi-step queries the dominant limit is model consistency rather than prompt quality, and the harness separates those genuine errors from over-strict scoring cleanly.

## Testing

98 test functions (114 cases with parametrization), with no network or API keys required external HTTP is mocked and the agent is faked, so the suite runs anywhere. CI runs the suite and `ruff` on every push via GitHub Actions.

```bash
pytest -q
```

## Stack

Python 3.12 · DuckDB · FastAPI · Streamlit · LangChain · Model Context Protocol · Gemini / Groq · pytest · ruff · GitHub Actions

## Data & attribution

Historical data: [Deutscher Wetterdienst Climate Data Center](https://opendata.dwd.de/climate_environment/CDC/), used under its [terms of use](https://opendata.dwd.de/climate_environment/CDC/Terms_of_use.pdf).
Live weather: [Open-Meteo](https://open-meteo.com/), CC BY 4.0.

## License

MIT
