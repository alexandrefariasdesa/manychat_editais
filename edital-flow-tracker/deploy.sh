#!/usr/bin/env bash
# Deploy do edital-flow-tracker. Rode DEPOIS de `npx wrangler login`.
# Injeta os secrets a partir de ../service_account.json e ./.dev.vars
# (nada de segredo é commitado). Idempotente — pode rodar de novo.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .dev.vars ] || { echo "faltando .dev.vars (SHARED_TOKEN/SHEET_ID)"; exit 1; }
[ -f ../service_account.json ] || { echo "faltando ../service_account.json"; exit 1; }

# shellcheck disable=SC1091
source .dev.vars

SA_EMAIL=$(py -3 -c "import json;print(json.load(open('../service_account.json'))['client_email'])")

# 1) deploy primeiro (cria o Worker) — assim `secret put` não pede confirmação
echo "→ deploy inicial"
npx --yes wrangler deploy

# 2) injeta os secrets no Worker já existente
put() { echo "→ secret $1"; printf '%s' "$2" | npx --yes wrangler secret put "$1" >/dev/null; }

put SHEET_ID       "$SHEET_ID"
put SHARED_TOKEN   "$SHARED_TOKEN"
put SA_EMAIL       "$SA_EMAIL"
# private_key com quebras de linha reais
py -3 -c "import json;import sys;sys.stdout.write(json.load(open('../service_account.json'))['private_key'])" \
  | npx --yes wrangler secret put SA_PRIVATE_KEY >/dev/null
echo "→ secret SA_PRIVATE_KEY"

echo "✅ pronto. URL do Worker acima (https://edital-flow-tracker.<subdominio>.workers.dev)"
