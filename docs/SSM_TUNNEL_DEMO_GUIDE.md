# Complete ClickOps Demo Guide: Private RDS SSM Tunnel with 2-Layer 5-Minute Auto-Shutdown

This guide walks you step-by-step through building a **Production-Grade Private Database Access Architecture** from scratch in a fresh **AWS Sandbox** using **ClickOps** (AWS Management Console), and demonstrating the **2-layer automatic shutoff logic** (5-minute idle tunnel drop + 5-minute container auto-scale to 0).

---

## 🏛️ Architecture Overview

```
                                  [ YOUR LAPTOP ]
                                         │
                             DBeaver / GUI Client (127.0.0.1:15432)
                                         │
                        Local Terminal: aws ssm start-session
                                         │
                  ═══════════════ AWS BACKBONE (Encrypted WSS) ═══════════════
                                         │
                                [ AWS Systems Manager ]
                                         │
                                  [ VPC Private SG ]
                                         ▼
                            [ ECS Fargate: db-jump ]
                      (0 Inbound Ports Open / Outbound Only)
                      ┌────────────────────────────────────┐
                      │  • Layer 1: SSM Idle Timeout (5m)  │
                      │  • Layer 2: TCP Watchdog (5m idle) │
                      │    Auto-scales desired-count to 0  │
                      └──────────────────┬─────────────────┘
                                         │ (VPC internal TCP :5432)
                                         ▼
                             [ Private RDS PostgreSQL ]
                       (Publicly Accessible: NO / Port 5432)
```

---

## 📋 Prerequisites
1. Fresh **A Cloud Guru AWS Sandbox** (Default region: `us-east-1`).
2. Local Laptop tools:
   - AWS CLI v2 (`aws --version`)
   - AWS Session Manager Plugin (`session-manager-plugin --version`)
   - DBeaver (or any PostgreSQL GUI)
   - Docker installed locally (to build and push the jump container to ECR).

---

## Part 1: ClickOps Network & Security Groups Setup

### 1.1 Locate Your VPC
1. Open the AWS Console and search for **VPC**.
2. Note your **Default VPC ID** (e.g., `vpc-0123456789abcdef0`).

### 1.2 Create Security Group 1: `demo-db-jump-sg` (For Fargate Jump Task)
1. In the VPC Console, click **Security groups** (left menu) $\to$ **Create security group**.
2. **Security group name**: `demo-db-jump-sg`
3. **Description**: `SG for ECS db-jump task - outbound only`
4. **VPC**: Select your default VPC.
5. **Inbound rules**: **Leave completely EMPTY** (0 inbound rules — no open ports!).
6. **Outbound rules**: Default `All traffic` (0.0.0.0/0) is fine.
7. Click **Create security group**. Note its ID (e.g., `sg-jump123`).

### 1.3 Create Security Group 2: `demo-rds-sg` (For PostgreSQL)
1. Click **Create security group**.
2. **Security group name**: `demo-rds-sg`
3. **Description**: `SG for RDS private access from db-jump only`
4. **VPC**: Select your default VPC.
5. **Inbound rules**:
   - Click **Add rule**.
   - **Type**: `PostgreSQL` (Port `5432`).
   - **Source**: Custom $\to$ Select `demo-db-jump-sg` (`sg-jump123`).
6. Click **Create security group**. Note its ID (e.g., `sg-rds123`).

---

## Part 2: ClickOps Private RDS PostgreSQL Setup

1. Search for **RDS** in the AWS Console.
2. Click **Databases** $\to$ **Create database**.
3. Choose database creation method: **Standard create**.
4. **Engine options**: **PostgreSQL** (version: default, e.g., 16 or 15).
5. **Templates**: **Free tier** (uses `db.t3.micro` or `db.t4g.micro`).
6. **Settings**:
   - **DB instance identifier**: `demo-postgres`
   - **Master username**: `postgres`
   - **Master password**: `YourSecurePassword123!` (or your choice).
7. **Instance configuration**: `db.t3.micro` or `db.t4g.micro`.
8. **Connectivity**:
   - **Virtual private cloud (VPC)**: Select Default VPC.
   - **Public access**: Choose **No** (CRITICAL — Private database).
   - **VPC security group (firewall)**: Choose **Choose existing**, remove default SG, and select **`demo-rds-sg`**.
   - **Availability Zone**: `No preference`.
   - **Database port**: `5432`.
9. **Additional configuration**:
   - **Initial database name**: `demo_db`
10. Click **Create database**. *(Takes 5–8 minutes to become `Available`. Note the Endpoint when ready).*

---

## Part 3: ClickOps IAM Roles Setup

We need two roles:
1. `demoEcsTaskExecutionRole`: To pull the image and push logs.
2. `demoDbJumpTaskRole`: For ECS Exec SSM channel + self-scale-down to 0.

### 3.1 Create `demoEcsTaskExecutionRole`
1. Go to **IAM** Console $\to$ **Roles** $\to$ **Create role**.
2. **Trusted entity type**: **AWS service** $\to$ **Use case**: **Elastic Container Service** $\to$ select **Elastic Container Service Task**.
3. Click **Next**.
4. In permissions search, select: **`AmazonECSTaskExecutionRolePolicy`**.
5. Click **Next**.
6. **Role name**: `demoEcsTaskExecutionRole`.
7. Click **Create role**.

### 3.2 Create `demoDbJumpTaskRole` (With ECS Exec + Self-Scale Permission)
1. Go to **IAM** $\to$ **Policies** $\to$ **Create policy**.
2. Switch to the **JSON** tab and paste:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcsExecSsmPermissions",
      "Effect": "Allow",
      "Action": [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowSelfScaleToZero",
      "Effect": "Allow",
      "Action": [
        "ecs:UpdateService",
        "ecs:DescribeServices"
      ],
      "Resource": "*"
    }
  ]
}
```
3. Click **Next**.
4. **Policy name**: `demoDbJumpTaskPolicy`.
5. Click **Create policy**.
6. Now go to **Roles** $\to$ **Create role**.
7. **Trusted entity**: **AWS service** $\to$ **Elastic Container Service** $\to$ **Elastic Container Service Task**.
8. Click **Next**. Attach **`demoDbJumpTaskPolicy`**.
9. **Role name**: `demoDbJumpTaskRole`.
10. Click **Create role**.

---

## Part 4: Configure Layer 1 — AWS SSM 5-Minute Idle Timeout (ClickOps)

This is the native AWS feature that terminates the tunnel if no data flows for 5 minutes.

1. In the AWS Console, search for **Systems Manager**.
2. In the left navigation menu under **Node Management**, click **Session Manager**.
3. Select the **Preferences** tab.
4. Click **Edit**.
5. Find **Idle session timeout**.
6. Change the value to **`5`** minutes.
7. Click **Save**.

*Layer 1 is now active across your AWS account.*

---

## Part 5: Build & Push `db-jump` Image with Layer 2 Watchdog

The container runs a background watchdog that monitors TCP connections to port 5432. If connections remain at 0 for 5 minutes, it calls `aws ecs update-service --desired-count 0`.

### 5.1 Create ECR Repository (ClickOps)
1. Search for **ECR** (Elastic Container Registry) in AWS Console.
2. Click **Repositories** $\to$ **Create repository**.
3. **Repository name**: `demo-db-jump`.
4. Leave other settings default $\to$ Click **Create repository**.
5. Note the **URI** (e.g. `123456789012.dkr.ecr.us-east-1.amazonaws.com/demo-db-jump`).

### 5.2 Build Container Image Locally
Create a temporary working folder on your laptop:

```bash
mkdir -p ~/demo-db-jump && cd ~/demo-db-jump
```

Create `entrypoint.sh`:
```bash
cat << 'EOF' > entrypoint.sh
#!/bin/bash
set -e

CLUSTER_NAME="${CLUSTER_NAME:-demo-cluster}"
SERVICE_NAME="${SERVICE_NAME:-db-jump}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
IDLE_LIMIT=300       # 5 minutes (300 seconds)
CHECK_INTERVAL=30    # Check every 30 seconds
IDLE_SECS=0

echo "=== [WATCHDOG] Starting db-jump watchdog ==="
echo "Target Cluster: $CLUSTER_NAME | Service: $SERVICE_NAME | Idle Limit: ${IDLE_LIMIT}s"

while true; do
  sleep $CHECK_INTERVAL

  # Check active TCP connections to RDS PostgreSQL port 5432
  ACTIVE_CONNS=$(ss -tn state established '( dport = :5432 )' 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')

  if [ "$ACTIVE_CONNS" -gt 0 ]; then
    IDLE_SECS=0
    echo "=== [WATCHDOG] Active DB connection detected ($ACTIVE_CONNS). Idle timer reset to 0s."
  else
    IDLE_SECS=$((IDLE_SECS + CHECK_INTERVAL))
    echo "=== [WATCHDOG] 0 active DB connections. Idle for ${IDLE_SECS}s / ${IDLE_LIMIT}s..."

    if [ "$IDLE_SECS" -ge "$IDLE_LIMIT" ]; then
      echo "=== [WATCHDOG] 5 minutes of inactivity reached! Auto-scaling ${SERVICE_NAME} to 0 ==="
      aws ecs update-service \
        --cluster "$CLUSTER_NAME" \
        --service "$SERVICE_NAME" \
        --desired-count 0 \
        --region "$AWS_DEFAULT_REGION"
      echo "=== [WATCHDOG] Scaling request sent. Exiting container ==="
      exit 0
    fi
  fi
done
EOF
chmod +x entrypoint.sh
```

Create `Dockerfile`:
```dockerfile
cat << 'EOF' > Dockerfile
FROM alpine:3.21

# Install bash, aws-cli, iproute2 (for ss), and postgresql-client
RUN apk add --no-cache bash curl aws-cli iproute2 postgresql-client

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
EOF
```

### 5.3 Push to ECR
Replace `<ACCOUNT_ID>` and `<REGION>` with your CloudGuru sandbox credentials:
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/demo-db-jump"

# 1. Login to ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# 2. Build for linux/amd64 or linux/arm64 (Fargate supports both, amd64 is easiest)
docker build --platform linux/amd64 -t demo-db-jump .

# 3. Tag and Push
docker tag demo-db-jump:latest ${ECR_URI}:latest
docker push ${ECR_URI}:latest
```

---

## Part 6: ClickOps ECS Cluster, Task Definition & Service Setup

### 6.1 Create ECS Cluster
1. Search for **ECS** in the AWS Console.
2. Click **Clusters** $\to$ **Create cluster**.
3. **Cluster name**: `demo-cluster`.
4. **Infrastructure**: Check **AWS Fargate (serverless)**.
5. Click **Create**.

### 6.2 Create Task Definition
1. In ECS Console, click **Task definitions** (left menu) $\to$ **Create new task definition** $\to$ **Create new task definition with JSON**.
2. Replace `<ACCOUNT_ID>`, `<REGION>`, and `<ECR_URI>` with your values:
```json
{
  "family": "demo-db-jump",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/demoEcsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/demoDbJumpTaskRole",
  "containerDefinitions": [
    {
      "name": "db-jump",
      "image": "<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/demo-db-jump:latest",
      "essential": true,
      "environment": [
        {"name": "CLUSTER_NAME", "value": "demo-cluster"},
        {"name": "SERVICE_NAME", "value": "db-jump"},
        {"name": "AWS_DEFAULT_REGION", "value": "us-east-1"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/demo-db-jump",
          "awslogs-region": "us-east-1",
          "awslogs-create-group": "true",
          "awslogs-stream-prefix": "jump"
        }
      }
    }
  ]
}
```
3. Click **Create**.

### 6.3 Create ECS Service (With Exec Enabled)
1. Open CloudShell or your local terminal to create the service with `--enable-execute-command` (ClickOps console does not expose this toggle during creation):
```bash
# Get your default VPC Subnet IDs
SUBNET_1=$(aws ec2 describe-subnets --filters "Name=default-for-az,Values=true" --query 'Subnets[0].SubnetId' --output text)
SUBNET_2=$(aws ec2 describe-subnets --filters "Name=default-for-az,Values=true" --query 'Subnets[1].SubnetId' --output text)
JUMP_SG=$(aws ec2 describe-security-groups --group-names "demo-db-jump-sg" --query 'SecurityGroups[0].GroupId' --output text)

aws ecs create-service \
  --cluster demo-cluster \
  --service-name db-jump \
  --task-definition demo-db-jump \
  --desired-count 0 \
  --launch-type FARGATE \
  --enable-execute-command \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_1,$SUBNET_2],securityGroups=[$JUMP_SG],assignPublicIp=ENABLED}"
```
*(Note: `assignPublicIp=ENABLED` is required on default VPC subnets so Fargate can reach SSM and ECR without paying for private VPC endpoints. However, because `demo-db-jump-sg` has 0 inbound rules, the container has NO open ports to the internet!)*

---

## Part 7: The Live Demo Walkthrough (Step-by-Step)

Here is the exact demonstration script to show the team or evaluator:

### Step 1: Scale Up On-Demand
Show that the service is currently at `0` tasks (costs $0):
```bash
aws ecs describe-services --cluster demo-cluster --services db-jump --query 'services[0].{Desired:desiredCount,Running:runningCount}'
```
Now, scale it up to 1:
```bash
aws ecs update-service --cluster demo-cluster --service db-jump --desired-count 1
```

### Step 2: Establish the SSM Tunnel
Run the following script to wait for the task to be ready and start the port-forwarding session:
```bash
CL="demo-cluster"
RDS_ENDPOINT="<PASTE_YOUR_RDS_ENDPOINT_HERE>" # e.g. demo-postgres.abcdef123456.us-east-1.rds.amazonaws.com

echo "Waiting for task to be RUNNING..."
until TASK_ARN="$(aws ecs list-tasks --cluster $CL --service-name db-jump --desired-status RUNNING --query 'taskArns[0]' --output text)"; [ "$TASK_ARN" != "None" ] && [ -n "$TASK_ARN" ]; do sleep 5; done
aws ecs wait tasks-running --cluster $CL --tasks "$TASK_ARN"

until [ "$(aws ecs describe-tasks --cluster $CL --tasks "$TASK_ARN" --query "tasks[0].containers[?name=='db-jump'] | [0].managedAgents[?name=='ExecuteCommandAgent'] | [0].lastStatus" --output text)" = "RUNNING" ]; do sleep 5; done
RUNTIME_ID="$(aws ecs describe-tasks --cluster $CL --tasks "$TASK_ARN" --query "tasks[0].containers[?name=='db-jump'] | [0].runtimeId" --output text)"

echo "Opening SSM Tunnel to localhost:15432..."
aws ssm start-session \
  --target "ecs:${CL}_${TASK_ARN##*/}_${RUNTIME_ID}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"${RDS_ENDPOINT}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"15432\"]}"
```
You will see:
`Waiting for connections...`
`Connection accepted for session [session-id]...`

### Step 3: Connect in DBeaver
1. Open DBeaver $\to$ **New Database Connection** $\to$ **PostgreSQL**.
2. **Host**: `127.0.0.1` *(Never the RDS address!)*
3. **Port**: `15432`
4. **Database**: `demo_db` (or `postgres`)
5. **Username**: `postgres`
6. **Password**: `YourSecurePassword123!`
7. Click **Test Connection** $\to$ **Connected successfully!**
8. Run a test SQL query: `SELECT NOW();`

---

### Step 4: DEMO LAYER 1 — 5-Minute Inactive Tunnel Drop
1. Stop running queries in DBeaver (or disconnect DBeaver).
2. Do not touch the terminal.
3. Observe:
   - At the **5-minute mark** of inactivity, AWS SSM forcibly drops the tunnel from the cloud side.
   - The terminal outputs:
     `Session closed due to inactivity`
     `Exiting session with status: Complete`
   - Port `15432` on your laptop is automatically closed and freed!

---

### Step 5: DEMO LAYER 2 — 5-Minute Inactivity Auto-Scale to 0
1. Open **CloudWatch Logs** $\to$ `/ecs/demo-db-jump`.
2. Observe the watchdog log outputs:
   ```
   === [WATCHDOG] 0 active DB connections. Idle for 60s / 300s...
   === [WATCHDOG] 0 active DB connections. Idle for 180s / 300s...
   === [WATCHDOG] 0 active DB connections. Idle for 300s / 300s...
   === [WATCHDOG] 5 minutes of inactivity reached! Auto-scaling db-jump to 0 ===
   ```
3. Open **ECS Console** $\to$ Clusters $\to$ `demo-cluster` $\to$ Services $\to$ `db-jump`.
4. Notice that:
   - **Desired count** is automatically updated to **`0`**.
   - The Fargate task gracefully stops.
   - AWS Fargate compute billing stops completely ($0.00).

---

## 🎯 Key Takeaways for the Demo
* **Zero Open Inbound Ports:** The Fargate jump container has no inbound ports open in its security group. All traffic goes through encrypted AWS SSM WebSockets.
* **Private RDS:** The database is in a private subnet / security group and cannot be reached from the public internet.
* **Double Protection (2-Layer):**
  1. If an engineer walks away with DBeaver open $\to$ Layer 1 cuts the tunnel after 5 mins.
  2. Once the tunnel cuts (or if the laptop lid is closed) $\to$ Layer 2 shuts down the ECS container after 5 mins.
* **$0 Overhead:** No Lambda functions, no EventBridge rules, no extra CloudWatch alarms. Everything is self-contained in the Fargate container and SSM preferences.
