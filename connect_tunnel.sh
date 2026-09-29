#!/usr/bin/env bash
# Operator tunnel script for demo db-jump
set -euo pipefail

CLUSTER="${CLUSTER:-demo-cluster}"
SERVICE="${SERVICE:-db-jump}"
LOCAL_PORT="${LOCAL_PORT:-15432}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

echo "=== [1/4] Fetching RDS Address from Pulumi Stack ==="
if command -v pulumi >/dev/null 2>&1; then
  RDS_HOST="$(pulumi stack output rds_address 2>/dev/null || true)"
fi

if [[ -z "${RDS_HOST:-}" ]]; then
  echo -n "Enter RDS Host endpoint: "
  read -r RDS_HOST
fi

echo "RDS Target: ${RDS_HOST}:5432"
echo "Local Port: 127.0.0.1:${LOCAL_PORT}"

echo "=== [2/4] Ensuring db-jump Task is RUNNING ==="
RUNNING_TASK="$(aws ecs list-tasks --cluster "${CLUSTER}" --service-name "${SERVICE}" --desired-status RUNNING --region "${REGION}" --query 'taskArns[0]' --output text)"

if [[ "${RUNNING_TASK}" == "None" || -z "${RUNNING_TASK}" ]]; then
  echo "Service is currently at 0 tasks. Scaling to 1..."
  aws ecs update-service --cluster "${CLUSTER}" --service "${SERVICE}" --desired-count 1 --region "${REGION}" >/dev/null
  echo "Waiting for task to launch..."
  until RUNNING_TASK="$(aws ecs list-tasks --cluster "${CLUSTER}" --service-name "${SERVICE}" --desired-status RUNNING --region "${REGION}" --query 'taskArns[0]' --output text)"; [ "${RUNNING_TASK}" != "None" ] && [ -n "${RUNNING_TASK}" ]; do
    echo "Waiting for task..."
    sleep 5
  done
  aws ecs wait tasks-running --cluster "${CLUSTER}" --tasks "${RUNNING_TASK}" --region "${REGION}"
fi

echo "Task RUNNING: ${RUNNING_TASK##*/}"

echo "=== [3/4] Waiting for ExecuteCommandAgent ==="
until [ "$(aws ecs describe-tasks --cluster "${CLUSTER}" --tasks "${RUNNING_TASK}" --region "${REGION}" --query "tasks[0].containers[?name=='db-jump'] | [0].managedAgents[?name=='ExecuteCommandAgent'] | [0].lastStatus" --output text 2>/dev/null || true)" = "RUNNING" ]; do
  echo "Waiting for agent to initialize..."
  sleep 5
done

RUNTIME_ID="$(aws ecs describe-tasks --cluster "${CLUSTER}" --tasks "${RUNNING_TASK}" --region "${REGION}" --query "tasks[0].containers[?name=='db-jump'] | [0].runtimeId" --output text)"

echo "=== [4/4] Starting SSM Port-Forwarding Tunnel ==="
echo "Leave this terminal open while using DBeaver / psql."
echo "Connect DBeaver to 127.0.0.1:${LOCAL_PORT}"
echo "--------------------------------------------------------"

aws ssm start-session \
  --region "${REGION}" \
  --target "ecs:${CLUSTER}_${RUNNING_TASK##*/}_${RUNTIME_ID}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"${RDS_HOST}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
