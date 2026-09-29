#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

echo "=== [1/4] Fetching ECR URL from Pulumi Stack ==="
if command -v pulumi >/dev/null 2>&1; then
  ECR_URL="$(pulumi stack output ecr_repository_url 2>/dev/null || true)"
fi

if [[ -z "${ECR_URL:-}" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  ECR_URL="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/demo-db-jump"
fi

echo "Target ECR: ${ECR_URL}:latest"

echo "=== [2/4] Logging in to Amazon ECR ==="
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_URL%%/*}"

echo "=== [3/4] Building Docker Image (linux/amd64) ==="
docker build --platform linux/amd64 -t demo-db-jump "${SCRIPT_DIR}/docker"

echo "=== [4/4] Tagging and Pushing Image to ECR ==="
docker tag demo-db-jump:latest "${ECR_URL}:latest"
docker push "${ECR_URL}:latest"

echo "=== Successfully pushed image to ECR! ==="
