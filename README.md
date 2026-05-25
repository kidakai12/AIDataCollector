# Programming Knowledge Collector (Mac M4)

Autonomous orchestration script that continuously extracts programming-related technical structures from a **local LLM** (Ollama) and stores them in **agentmemory** via **iii-sdk** (`mem::observe`) until disk space falls below a configurable floor (default **10 GB** free).

Optimized for Apple Silicon unified memory with a throttle between cycles.

## Architecture

```
┌─────────────────┐     HTTP      ┌──────────────┐
│ infinite_       │ ──────────────► │ Ollama       │
│ programmer.py   │                 │ (local LLM)  │
└────────┬────────┘                 └──────────────┘
         │ WebSocket ws://localhost:49134
         ▼
┌─────────────────┐
│ agentmemory     │  mem::observe → programming_knowledge_vault
│ (iii-engine)    │  metadata: type=programming_data_model
└─────────────────┘
```

## Project layout

```
AIDataCollect/
├── infinite_programmer.py   # Main orchestration loop
├── requirements.txt
├── .env.example
└── README.md
```

## Quick start (macOS / Apple Silicon)

### 1. Prerequisites

- **Homebrew** (optional but recommended)
- **Python 3.11+**
- **Ollama** for local inference
- **Node.js** for the agentmemory daemon

### 2. Install dependencies

```bash
cd /path/to/AIDataCollect

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env: set OLLAMA_MODEL, DISK_CHECK_PATH to your 1TB volume mount
```

### 3. Start backing services

Terminal A — **agentmemory** (iii WebSocket on port 49134):

```bash
npx -y @agentmemory/agentmemory
```

Terminal B — **Ollama**:

```bash
ollama serve
ollama pull llama3.2:latest   # or your preferred coding model
```

### 4. Run the collector

Terminal C:

```bash
source .venv/bin/activate
python infinite_programmer.py
```

The process loops until:

- Free space on `DISK_CHECK_PATH` drops below `MIN_FREE_GB` (default 10), or
- You send `Ctrl+C` / `SIGTERM`.

### 5. Verify observations

With agentmemory running, search the vault:

```python
from iii import register_worker

iii = register_worker("ws://localhost:49134")
iii.connect()
hits = iii.trigger({
    "function_id": "mem::smart-search",
    "payload": {
        "project": "programming_knowledge_vault",
        "query": "database schema normalization",
        "limit": 5,
    },
})
print(hits)
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `III_WS_URL` | `ws://localhost:49134` | iii-engine WebSocket |
| `MEMORY_PROJECT` | `programming_knowledge_vault` | agentmemory project name |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama API base |
| `OLLAMA_MODEL` | `llama3.2:latest` | Model tag for extraction |
| `DISK_CHECK_PATH` | `/` | Volume path for `shutil.disk_usage` |
| `MIN_FREE_GB` | `10` | Stop when free space below this |
| `CYCLE_SLEEP_SECONDS` | `2.5` | Throttle between cycles (M4 memory flush) |

Point `DISK_CHECK_PATH` at the mount point of your 1 TB drive (e.g. `/Volumes/Data`).

## Observation payload

Each cycle calls `mem::observe` with:

- `project`: `programming_knowledge_vault`
- `data.type`: `programming_data_model`
- `data.category`, `data.batch_index`, `data.content` (LLM extraction)

Filter and recall via agentmemory smart-search on that project name.
