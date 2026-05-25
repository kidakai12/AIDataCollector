#!/usr/bin/env python3
"""
Infinite programming-knowledge extractor for Apple Silicon Macs.

Continuously prompts a local LLM for production-grade technical structures,
then ingests observations into agentmemory via iii-sdk (mem::observe).

Prerequisites:
  - agentmemory daemon: npx -y @agentmemory/agentmemory
  - local LLM (Ollama): ollama serve && ollama pull <model>
  - pip install -r requirements.txt
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from iii import register_worker

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("infinite_programmer")

# --- Configuration (env overrides) ---

III_WS_URL = os.getenv("III_WS_URL", "ws://localhost:49134")
MEMORY_PROJECT = os.getenv("MEMORY_PROJECT", "programming_knowledge_vault")
SESSION_ID = os.getenv("SESSION_ID", "infinite-programmer-m4")
OBSERVATION_TYPE = "programming_data_model"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

DISK_CHECK_PATH = os.getenv("DISK_CHECK_PATH", "/")
MIN_FREE_GB = float(os.getenv("MIN_FREE_GB", "10"))
MIN_FREE_BYTES = int(MIN_FREE_GB * 1024**3)

CYCLE_SLEEP_SECONDS = float(os.getenv("CYCLE_SLEEP_SECONDS", "2.5"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "600"))

# Deep technical categories — rotated endlessly; batch_index selects subsection.
PROGRAMMING_TOPICS: list[str] = [
    "distributed_systems_consensus_and_replication",
    "relational_database_schemas_and_normalization",
    "nosql_document_and_wide_column_models",
    "event_sourcing_and_cqrs_command_event_shapes",
    "rest_openapi_and_graphql_schema_contracts",
    "grpc_protobuf_service_and_message_layouts",
    "message_queue_topology_and_delivery_semantics",
    "kubernetes_resource_specs_and_operators",
    "microservice_boundaries_and_saga_orchestration",
    "caching_layers_ttl_and_invalidation_policies",
    "authentication_oauth2_oidc_token_claims",
    "authorization_rbac_abac_policy_tables",
    "observability_metrics_logs_traces_schemas",
    "compiler_frontend_ast_and_ir_forms",
    "type_systems_generics_and_variance_rules",
    "memory_models_concurrency_and_lock_free_patterns",
    "storage_engines_btree_lsm_wal_formats",
    "search_inverted_index_and_ranking_features",
    "ml_pipeline_feature_store_and_model_cards",
    "security_threat_models_and_crypto_primitives",
    "api_rate_limiting_and_idempotency_keys",
    "data_warehouse_star_snowflake_schemas",
    "stream_processing_windowing_and_state_stores",
    "ci_cd_pipeline_artifacts_and_deployment_specs",
    "language_runtime_gc_and_ffi_boundaries",
]

_shutdown_requested = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    log.info("Received signal %s — finishing current cycle then exiting.", signum)
    _shutdown_requested = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def free_bytes_on_volume(path: str) -> int:
    """Return free bytes on the filesystem containing ``path``."""
    usage = shutil.disk_usage(path)
    return usage.free


def disk_guard_ok() -> bool:
    free = free_bytes_on_volume(DISK_CHECK_PATH)
    free_gb = free / (1024**3)
    if free < MIN_FREE_BYTES:
        log.warning(
            "Disk safeguard triggered: %.2f GB free on %s (floor %.1f GB). Stopping.",
            free_gb,
            DISK_CHECK_PATH,
            MIN_FREE_GB,
        )
        return False
    log.debug("Disk OK: %.2f GB free on %s", free_gb, DISK_CHECK_PATH)
    return True


def build_extraction_prompt(topic: str, batch_index: int) -> str:
    """Force structured, programming-only output — no conversational filler."""
    variant = batch_index % 12
    depth_hints = [
        "foundational definitions and canonical diagrams",
        "edge cases, failure modes, and recovery procedures",
        "performance characteristics and complexity bounds",
        "security implications and trust boundaries",
        "operational runbooks and SLO/SLI mappings",
        "cross-language implementation notes",
        "formal invariants and state-machine transitions",
        "migration and versioning strategies",
        "testing matrices and contract verification",
        "anti-patterns and known sharp edges",
        "reference implementations in pseudocode",
        "comparative analysis vs adjacent patterns",
    ]
    depth = depth_hints[variant]

    return f"""ROLE: You are a technical specification generator. Output ONLY machine-ingestible programming artifacts.

TOPIC: {topic}
SUBSECTION (batch {batch_index}): {depth}

STRICT OUTPUT RULES:
1. NO greetings, apologies, disclaimers, or conversational prose.
2. NO markdown headings like "Introduction" or "Conclusion".
3. Deliver at least TWO of: (a) DDL/SQL or JSON Schema, (b) ASCII or mermaid diagram, (c) typed pseudocode or interface blocks, (d) data tables with column types.
4. Every field, column, and type MUST be named explicitly.
5. Prefer YAML/JSON blocks, SQL CREATE TABLE, OpenAPI fragments, Protobuf-style messages, or AST node enumerations as appropriate to the topic.
6. Maximum density: production-grade specs a senior engineer could implement from directly.

Begin output immediately with structured content."""


def call_local_llm(prompt: str) -> str:
    """Generate via Ollama-compatible /api/generate endpoint."""
    url = f"{OLLAMA_BASE_URL}/api/generate"
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.35,
            "num_predict": 4096,
        },
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=body)
        response.raise_for_status()
        data = response.json()
    text = (data.get("response") or "").strip()
    if not text:
        raise RuntimeError("Local LLM returned empty response")
    return text


def observe_extraction(
    iii: Any,
    *,
    topic: str,
    batch_index: int,
    content: str,
) -> dict[str, Any]:
    """Persist extraction via agentmemory mem::observe."""
    title = f"{topic}::batch_{batch_index}"
    payload = {
        "hookType": "ProgrammingModelExtract",
        "sessionId": SESSION_ID,
        "project": MEMORY_PROJECT,
        "cwd": os.getcwd(),
        "timestamp": now_iso(),
        "data": {
            "type": OBSERVATION_TYPE,
            "title": title,
            "category": topic,
            "batch_index": batch_index,
            "model": OLLAMA_MODEL,
            "content": content,
        },
    }
    return iii.trigger({"function_id": "mem::observe", "payload": payload})


def connect_iii() -> Any:
    log.info("Connecting to iii at %s", III_WS_URL)
    worker = register_worker(III_WS_URL)
    worker.connect()
    return worker


def run_cycle(iii: Any, topic: str, batch_index: int) -> None:
    prompt = build_extraction_prompt(topic, batch_index)
    log.info("Extracting [%s] batch=%d via %s", topic, batch_index, OLLAMA_MODEL)

    content = call_local_llm(prompt)
    result = observe_extraction(
        iii,
        topic=topic,
        batch_index=batch_index,
        content=content,
    )
    obs_id = result.get("id") or result.get("observationId") or "ok"
    log.info("Observed %s chars → mem::observe (%s)", len(content), obs_id)


def main() -> int:
    log.info(
        "Starting infinite programmer | project=%s | disk_path=%s | min_free=%.1f GB",
        MEMORY_PROJECT,
        DISK_CHECK_PATH,
        MIN_FREE_GB,
    )

    if not disk_guard_ok():
        return 1

    try:
        iii = connect_iii()
    except Exception as exc:
        log.error(
            "Failed to connect to iii (%s). Is agentmemory running? %s",
            III_WS_URL,
            exc,
        )
        return 1

    topic_index = 0
    batch_index = 0

    while not _shutdown_requested:
        if not disk_guard_ok():
            break

        topic = PROGRAMMING_TOPICS[topic_index % len(PROGRAMMING_TOPICS)]
        topic_index += 1

        try:
            run_cycle(iii, topic, batch_index)
        except httpx.HTTPError as exc:
            log.error("LLM HTTP error: %s — retrying next cycle.", exc)
        except Exception as exc:
            log.error("Cycle failed: %s — retrying next cycle.", exc, exc_info=True)

        batch_index += 1
        time.sleep(CYCLE_SLEEP_SECONDS)

    log.info("Shutdown complete after %d extractions.", batch_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
