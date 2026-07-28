# SHIELD: Self-Healing, Instrumented, Economical, LLM-driven Data-engineering

Status: In development (Phase 0 complete — environment setup)

## Project Structure
- agents/ — per-agent implementation modules
- orchestration/ — LangGraph / orchestrator configuration
- infra/ — Docker Compose, Airflow DAGs, Kafka configs
- datasets/ — dataset acquisition scripts and metadata (raw data not tracked)
- fault_injection/ — fault harness and injection profiles
- experiments/ — experiment configs and results
- formal_model/ — MAPE-K formalization and convergence validation
- notebooks/ — analysis notebooks
- paper/ — manuscript drafts
- docs/ — architecture notes, setup instructions

## Environment
- Conda env: `shield-env` (Python 3.11)
- Hardware: Intel i9-10900X, 31GB RAM, RTX 3070 8GB VRAM
- Local LLMs (Ollama): llama3.2:3b, gemma3:4b, qwen2.5:7b, llama3:8b-instruct-q4_K_M, llama3.1:8b, qwen2.5:14b, gemma4:e4b
