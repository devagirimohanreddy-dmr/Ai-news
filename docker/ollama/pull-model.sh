#!/bin/bash
# =============================================================================
# pull-model.sh — Download the default LLM into the Ollama container
# =============================================================================
#
# Purpose:
#   Pulls a lightweight Llama 3.2 3B model into the running Ollama container
#   for use in local classification / summarisation tasks.
#
# Prerequisites:
#   - The ollama service must already be running:
#       docker compose up -d ollama
#
# Usage:
#   bash docker/ollama/pull-model.sh
#
# =============================================================================

set -euo pipefail

echo "Pulling Llama 3.2 3B model for local inference..."
docker exec ai-news-aggregator-bot-ollama-1 ollama pull llama3.2:3b
echo "Model pulled successfully."
