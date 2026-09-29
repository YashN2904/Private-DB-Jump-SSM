# 🔐 AWS Private RDS via SSM Port Forwarding & 2-Layer Auto-Shutdown

[![Pulumi](https://img.shields.io/badge/IaC-Pulumi%20Python-8A3391?logo=pulumi&logoColor=white)](https://www.pulumi.com/)
[![AWS](https://img.shields.io/badge/AWS-Fargate%20%7C%20RDS%20%7C%20SSM-232F3E?logo=amazon-aws&logoColor=white)](https://aws.amazon.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An enterprise-grade, zero-trust infrastructure pattern for accessing **100% private AWS RDS databases** without public IPs, SSH keys, bastion EC2 instances, or vulnerable `/32` IP allowlists. 

Features an automated **2-layer inactivity shutoff mechanism** that drops idle client tunnels and scales the Fargate jump container down to **0 tasks**, guaranteeing **$0.00 idle compute cost**.

---

## 🏛️ Architecture Overview

Traditional bastion host setups require public subnets, open inbound SSH ports (22), and ongoing maintenance. This solution replaces that legacy pattern entirely:

```
                              [ Operator Laptop ]
                                       │
                      DBeaver / GUI Client (127.0.0.1:15432)
                                       │
                      Local Terminal: ./connect_tunnel.sh
                                       │
             ═════════════ AWS BACKBONE (Encrypted WSS) ═════════════
                                       │
                            [ AWS Systems Manager ]
                                       │
                           [ demo-db-jump-sg (VPC) ]
                                       ▼
                       [ ECS Fargate Task: db-jump ]
                 (0 Inbound Ports Open / Outbound SSM Only)
                 ┌────────────────────────────────────────┐
                 │  • Layer 1: SSM Idle Timeout (5 mins)  │
                 │  • Layer 2: TCP Watchdog (5 mins idle) │
                 │    Auto-scales desired-count to 0      │
                 └───────────────────┬────────────────────┘
                                     │ (VPC internal TCP :5432)
                                     ▼
                         [ Private RDS PostgreSQL ]
                    (Publicly Accessible: NO / Port 5432)
```

### 🔒 Key Security Highlights
1. **Zero Open Inbound Ports:** The Fargate jump task security group has **0 inbound rules**. Inbound ingress from the public internet or VPC is impossible.
2. **Outbound-Only Communication:** The task establishes an outbound TLS WebSocket connection to AWS Systems Manager endpoints.
3. **No SSH Keys to Manage:** Authentication is gated strictly by AWS IAM credentials and temporary STS tokens.
4. **Strict DB Isolation:** The RDS security group permits TCP traffic on port `5432` *exclusively* from the jump host security group.

---

## ⚡ The 2-Layer Auto-Shutdown Mechanism

Running 24/7 jump hosts wastes money and exposes persistent connections. This project enforces two independent shutdown layers:

```
[ Active Querying ]
        │
        ▼ (Operator stops querying / disconnects GUI)
┌────────────────────────────────────────────────────────┐
│  Layer 1: AWS SSM Session Timeout (5 Minutes)          │
│  AWS Session Manager monitors packet flow. If 0 bytes   │
│  flow across the WSS tunnel for 5 minutes, AWS forcibly │
│  terminates the session and frees local port 15432.    │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼ (5 minutes idle on port 5432)
┌────────────────────────────────────────────────────────┐
│  Layer 2: In-Container TCP Watchdog (300 Seconds)      │
│  A background daemon inside the container monitors     │
│  active TCP sockets to RDS (:5432). Once idle for 300s, │
│  it executes:                                          │
│    aws ecs update-service --desired-count 0            │
│  The container stops, returning compute cost to $0.00. │
└────────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```text
.
├── Pulumi.yaml              # Pulumi Project definition
├── Pulumi.example.yaml      # Configuration template with secret placeholder
├── requirements.txt         # Python dependencies (pulumi, pulumi-aws)
├── __main__.py              # Complete IaC: VPC lookup, SGs, RDS, IAM, SSM, ECS
├── push_image.sh            # Builds & pushes the watchdog container to ECR
├── connect_tunnel.sh        # One-command operator script (scales task & starts tunnel)
├── docker/
│   ├── Dockerfile           # Minimal Alpine 3.21 image with aws-cli, iproute2, psql
│   └── entrypoint.sh        # 300-second TCP inactivity watchdog daemon
└── docs/
    ├── DEMO_EXECUTION_AND_VERIFICATION_SUMMARY.md  # Detailed verification report
    └── CLOUDGURU_SSM_TUNNEL_DEMO_GUIDE.md          # Step-by-step ClickOps guide
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
Ensure you have the following installed on your local machine:
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) (`aws --version`)
- [AWS Session Manager Plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) (`session-manager-plugin --version`)
- [Pulumi CLI](https://www.pulumi.com/docs/install/) (`pulumi version`)
- [Docker Desktop](https://www.docker.com/) (running)
- Python 3.10+
- Database GUI client ([DBeaver](https://dbeaver.io/), TablePlus, or `psql`)

---

### 2. Configure AWS & Pulumi Stack

```bash
# 1. Clone repository and navigate to root
cd demo-pulumi-ssm-jump

# 2. Create Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Initialize your Pulumi stack (e.g., demo)
pulumi stack init demo

# 4. Set required configuration & secure DB password
pulumi config set aws:region us-east-1
pulumi config set db_name demo_db
pulumi config set db_user postgres
pulumi config set --secret db_password "YourStrongPasswordHere123!"
```

---

### 3. Deploy Infrastructure

Run `pulumi up` to preview and provision the resources:

```bash
pulumi up
```
*(Provisions 15 resources in ~5–6 minutes: Default VPC lookup, Private RDS PostgreSQL instance, Security Groups, IAM Roles, SSM Session Preferences, ECR, and ECS Cluster/Service with `desired_count=0`)*.

---

### 4. Build & Push Watchdog Container

Build the container image and push it to the newly created Amazon ECR repository:

```bash
chmod +x push_image.sh connect_tunnel.sh
./push_image.sh
```

---

### 5. Open the Secure Tunnel

To access the private database, run the connection script:

```bash
./connect_tunnel.sh
```

**What this script does automatically:**
1. Detects that the `db-jump` service is currently at `0` tasks.
2. Scales `db-jump` to `1` task on demand.
3. Waits until the Fargate task and `ExecuteCommandAgent` are in `RUNNING` status.
4. Opens an encrypted port-forwarding tunnel on `127.0.0.1:15432`.

---

### 6. Connect via Database GUI (DBeaver / TablePlus)

Open your preferred database client and configure the connection:

| Parameter | Value | Note |
|---|---|---|
| **Host** | `127.0.0.1` | **Always use localhost; never enter the AWS RDS host directly!** |
| **Port** | `15432` | The local port forwarded by SSM |
| **Database** | `demo_db` | Configured DB name |
| **Username** | `postgres` | Configured DB username |
| **Password** | *(Your secure password)* | Configured in step 2 |

Click **Test Connection** $\to$ **Connected successfully!**  
Test query:
```sql
SELECT version(), NOW();
```

---

## 🧪 Testing the 2-Layer Auto-Shutdown

### Test Layer 1: SSM Inactivity Drop (5 Minutes)
1. Keep the tunnel terminal and your database client open.
2. **Do not run any SQL queries for 5 minutes** (or close your GUI client).
3. **Observed Output:** Exactly at 5 minutes, AWS Systems Manager drops the connection from the cloud:
   ```text
   SessionId: session-xyz123 : Your session timed out due to inactivity and has been terminated.
   ```
   Local port `15432` is automatically unbound and freed.

---

### Test Layer 2: Fargate Task Self-Scale to 0 (5 Minutes)
1. Inspect CloudWatch logs under `/ecs/demo-db-jump`:
   ```bash
   aws logs filter-log-events --log-group-name /ecs/demo-db-jump --query 'events[].message' --output text
   ```
2. You will observe the watchdog timer counting inactivity:
   ```text
   [WATCHDOG] 0 active DB connections. Idle for 240s / 300s...
   [WATCHDOG] 0 active DB connections. Idle for 270s / 300s...
   [WATCHDOG] 0 active DB connections. Idle for 300s / 300s...
   =================================================================
   [WATCHDOG] 5 minutes of inactivity reached!
   [WATCHDOG] Auto-scaling db-jump in cluster demo-cluster to desired-count 0...
   =================================================================
   [WATCHDOG] Scale-down command executed successfully. Exiting container.
   ```
3. Check the ECS service status:
   ```bash
   aws ecs describe-services --cluster demo-cluster --services db-jump --query 'services[0].{Desired:desiredCount,Running:runningCount}'
   ```
   **Output:** `{"Desired": 0, "Running": 0}` $\to$ **$0.00 idle compute cost.**

---

## 🧹 Teardown

When you are finished testing or wish to destroy all provisioned AWS resources:

```bash
pulumi destroy --yes
```
*(Cleanly deletes the RDS instance, ECS service, cluster, IAM roles, ECR repository, CloudWatch log group, and security groups in a single automated pass).*

To delete the stack configuration:
```bash
pulumi stack rm demo --yes
```

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
