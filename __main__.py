"""
Pulumi Infrastructure Program for:
Private RDS PostgreSQL + ECS Fargate Jump Host (SSM Port Forwarding)
with 2-Layer 5-Minute Inactivity Auto-Shutdown.

Replicates modern secure database access architecture:
- Private RDS PostgreSQL (no public IP, port 5432 ingress only from Jump SG)
- Fargate Jump Host db-jump (0 inbound ports open, outbound SSM only)
- Layer 1: AWS SSM Session Manager 5-minute idle timeout
- Layer 2: In-container TCP watchdog that auto-scales db-jump desired-count to 0
"""

import json
import pulumi
import pulumi_aws as aws

# ---------------------------------------------------------------------------
# Configuration & Defaults
# ---------------------------------------------------------------------------
config = pulumi.Config()
aws_config = pulumi.Config("aws")
region = aws_config.get("region") or "us-east-1"

db_name = config.get("db_name") or "demo_db"
db_user = config.get("db_user") or "postgres"
db_password = config.get("db_password") or "ChangeMeToAStrongPassword123!"

# ---------------------------------------------------------------------------
# VPC & Subnets (Discover Default VPC)
# ---------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
subnets = aws.ec2.get_subnets(filters=[
    aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id])
])

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------
# 1. Jump SG: 0 INBOUND rules (completely locked down). Outbound all.
jump_sg = aws.ec2.SecurityGroup(
    "demo-db-jump-sg",
    description="Security group for ECS db-jump Fargate task - outbound only",
    vpc_id=default_vpc.id,
    ingress=[],  # 0 inbound ports open!
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound to SSM and RDS",
        )
    ],
    tags={"Name": "demo-db-jump-sg"},
)

# 2. RDS SG: Inbound 5432 ONLY from jump_sg.
rds_sg = aws.ec2.SecurityGroup(
    "demo-rds-sg",
    description="Security group for RDS PostgreSQL - ingress from db-jump only",
    vpc_id=default_vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=5432,
            to_port=5432,
            security_groups=[jump_sg.id],
            description="PostgreSQL port 5432 from db-jump Fargate task only",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Outbound",
        )
    ],
    tags={"Name": "demo-rds-sg"},
)

# ---------------------------------------------------------------------------
# Private RDS PostgreSQL Instance
# ---------------------------------------------------------------------------
rds_subnet_group = aws.rds.SubnetGroup(
    "demo-rds-subnet-group",
    subnet_ids=subnets.ids,
    tags={"Name": "demo-rds-subnet-group"},
)

rds_instance = aws.rds.Instance(
    "demo-postgres",
    identifier="demo-postgres",
    engine="postgres",
    instance_class="db.t3.micro",
    allocated_storage=20,
    max_allocated_storage=50,
    storage_type="gp3",
    db_name=db_name,
    username=db_user,
    password=db_password,
    db_subnet_group_name=rds_subnet_group.name,
    vpc_security_group_ids=[rds_sg.id],
    publicly_accessible=False,  # CRITICAL: Private database
    skip_final_snapshot=True,
    tags={"Name": "demo-postgres"},
)

# ---------------------------------------------------------------------------
# IAM Roles for ECS Fargate
# ---------------------------------------------------------------------------
# 1. Execution Role (Pulls image from ECR, pushes CloudWatch logs)
ecs_execution_role = aws.iam.Role(
    "demoEcsTaskExecutionRole",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
        }],
    }),
    tags={"Name": "demoEcsTaskExecutionRole"},
)

aws.iam.RolePolicyAttachment(
    "demoEcsTaskExecutionRolePolicyAttachment",
    role=ecs_execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# 2. Task Role (ECS Exec / SSM channels + self-scale-down to desired-count 0)
ecs_task_role = aws.iam.Role(
    "demoDbJumpTaskRole",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
        }],
    }),
    tags={"Name": "demoDbJumpTaskRole"},
)

aws.iam.RolePolicy(
    "demoDbJumpTaskPolicy",
    role=ecs_task_role.id,
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EcsExecSsmChannelPermissions",
                "Effect": "Allow",
                "Action": [
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                "Resource": "*",
            },
            {
                "Sid": "AllowSelfScaleToZero",
                "Effect": "Allow",
                "Action": [
                    "ecs:UpdateService",
                    "ecs:DescribeServices",
                ],
                "Resource": "*",
            },
        ],
    }),
)

# ---------------------------------------------------------------------------
# LAYER 1: AWS Systems Manager 5-Minute Idle Session Timeout
# ---------------------------------------------------------------------------
# Configures the native SSM SessionManager preferences document
ssm_session_preferences = aws.ssm.Document(
    "demoSsmSessionPreferences",
    name="SSM-SessionManagerRunShell",
    document_type="Session",
    document_format="JSON",
    content=json.dumps({
        "schemaVersion": "1.0",
        "description": "Session Manager Preferences: 5-minute idle timeout",
        "sessionType": "Standard_Stream",
        "inputs": {
            "idleSessionTimeout": "5",  # Terminate tunnel after 5 min inactivity
        },
    }),
    tags={"Name": "SSM-SessionManagerRunShell"},
)

# ---------------------------------------------------------------------------
# ECR Repository & CloudWatch Logs for db-jump
# ---------------------------------------------------------------------------
ecr_repo = aws.ecr.Repository(
    "demo-db-jump-repo",
    name="demo-db-jump",
    force_delete=True,
    image_tag_mutability="MUTABLE",
    tags={"Name": "demo-db-jump"},
)

log_group = aws.cloudwatch.LogGroup(
    "demo-db-jump-logs",
    name="/ecs/demo-db-jump",
    retention_in_days=1,
    tags={"Name": "demo-db-jump-logs"},
)

# ---------------------------------------------------------------------------
# ECS Cluster, Task Definition & Fargate Service
# ---------------------------------------------------------------------------
ecs_cluster = aws.ecs.Cluster(
    "demo-cluster",
    name="demo-cluster",
    tags={"Name": "demo-cluster"},
)

# Container definition: Uses image from ECR, passes cluster & service names
container_definitions_json = pulumi.Output.all(
    ecr_repo.repository_url,
    ecs_cluster.name,
).apply(
    lambda args: json.dumps([{
        "name": "db-jump",
        "image": f"{args[0]}:latest",
        "essential": True,
        "environment": [
            {"name": "CLUSTER_NAME", "value": args[1]},
            {"name": "SERVICE_NAME", "value": "db-jump"},
            {"name": "AWS_DEFAULT_REGION", "value": region},
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "/ecs/demo-db-jump",
                "awslogs-region": region,
                "awslogs-create-group": "true",
                "awslogs-stream-prefix": "jump",
            },
        },
    }])
)

task_definition = aws.ecs.TaskDefinition(
    "demo-db-jump-td",
    family="demo-db-jump",
    cpu="256",
    memory="512",
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    execution_role_arn=ecs_execution_role.arn,
    task_role_arn=ecs_task_role.arn,
    container_definitions=container_definitions_json,
    tags={"Name": "demo-db-jump"},
)

# Fargate Service: desired_count defaults to 0; ignore_changes so scaling to 1 doesn't conflict
ecs_service = aws.ecs.Service(
    "demo-db-jump-service",
    name="db-jump",
    cluster=ecs_cluster.id,
    task_definition=task_definition.arn,
    desired_count=0,
    launch_type="FARGATE",
    enable_execute_command=True,  # CRITICAL: Enables ECS Exec for SSM Tunnel
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        subnets=subnets.ids,
        security_groups=[jump_sg.id],
        assign_public_ip=True,  # Enables outbound route to SSM without NAT gateway cost
    ),
    opts=pulumi.ResourceOptions(
        ignore_changes=["desired_count"],
        depends_on=[ssm_session_preferences],
    ),
    tags={"Name": "db-jump"},
)

# ---------------------------------------------------------------------------
# Stack Exports
# ---------------------------------------------------------------------------
pulumi.export("rds_instance_id", rds_instance.id)
pulumi.export("rds_address", rds_instance.address)
pulumi.export("rds_endpoint", rds_instance.endpoint)
pulumi.export("rds_db_name", db_name)
pulumi.export("rds_username", db_user)
pulumi.export("ecs_cluster_name", ecs_cluster.name)
pulumi.export("ecs_service_name", ecs_service.name)
pulumi.export("ecr_repository_url", ecr_repo.repository_url)
