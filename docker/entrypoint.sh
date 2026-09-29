#!/bin/bash
set -e

CLUSTER_NAME="${CLUSTER_NAME:-demo-cluster}"
SERVICE_NAME="${SERVICE_NAME:-db-jump}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
IDLE_LIMIT=300       # 5 minutes (300 seconds)
CHECK_INTERVAL=30    # Check every 30 seconds
IDLE_SECS=0

echo "================================================================="
echo "=== [WATCHDOG] Starting db-jump Inactivity Monitor"
echo "=== Cluster: ${CLUSTER_NAME} | Service: ${SERVICE_NAME} | Region: ${AWS_DEFAULT_REGION}"
echo "=== Inactivity Limit: ${IDLE_LIMIT}s (5 minutes)"
echo "================================================================="

while true; do
  sleep $CHECK_INTERVAL

  # Check active TCP connections established to PostgreSQL port 5432
  ACTIVE_CONNS=$(ss -tn state established '( dport = :5432 )' 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')

  if [ -n "$ACTIVE_CONNS" ] && [ "$ACTIVE_CONNS" -gt 0 ]; then
    IDLE_SECS=0
    echo "[$(date +'%T')] [WATCHDOG] Active DB connection detected ($ACTIVE_CONNS). Idle timer reset to 0s."
  else
    IDLE_SECS=$((IDLE_SECS + CHECK_INTERVAL))
    echo "[$(date +'%T')] [WATCHDOG] 0 active DB connections. Idle for ${IDLE_SECS}s / ${IDLE_LIMIT}s..."

    if [ "$IDLE_SECS" -ge "$IDLE_LIMIT" ]; then
      echo "================================================================="
      echo "[$(date +'%T')] [WATCHDOG] 5 minutes of inactivity reached!"
      echo "[$(date +'%T')] [WATCHDOG] Auto-scaling ${SERVICE_NAME} in cluster ${CLUSTER_NAME} to desired-count 0..."
      echo "================================================================="
      
      aws ecs update-service \
        --cluster "$CLUSTER_NAME" \
        --service "$SERVICE_NAME" \
        --desired-count 0 \
        --region "$AWS_DEFAULT_REGION"
      
      echo "[$(date +'%T')] [WATCHDOG] Scale-down command executed successfully. Exiting container."
      exit 0
    fi
  fi
done
