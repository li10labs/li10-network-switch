from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_logs as logs
)
from constructs import Construct

class Li10NetworkSwitchStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.template_options.description="Li10 Network Switch Stack - Switch NAT on/off"

        lambda_timeout = 4*60 # TODO move to param
        event_source_name = "li10-switch"
        event_detail_type = "li10-switch: turn NAT on or off"
        event_detail_trigger = {
            "source": [event_source_name],
            "detail": {
                "action": ["create", "delete"]
            }
        }

        log_group = logs.LogGroup(self, "Li10NetworkSwitchLogGroup",
            log_group_name="/aws/lambda/li10_network_switch",
            retention=logs.RetentionDays.ONE_WEEK
        )

        inline_policies = {
            'li10_network_switch_policy': iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        sid="li10NetworkSwitch",
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "ec2:CreateNatGateway",
                            "ec2:DeleteNatGateway",
                            "ec2:AllocateAddress",
                            "ec2:ReplaceRoute",
                            "ec2:DescribeRouteTables",
                            "ec2:CreateTags",
                            "ec2:DescribeSubnets",
                            "ec2:ReleaseAddress",
                            "ec2:DescribeNatGateways",
                            "ec2:DescribeAddresses",
                            "events:PutEvents"
                            ],
                        resources=["*"]
                    ),
                    iam.PolicyStatement(
                        sid="lambdaExecution",
                        effect=iam.Effect.ALLOW,
                        actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                        resources=[
                            log_group.log_group_arn,
                            f"{log_group.log_group_arn}:*"
                        ]
                    ),
                ]
            )
        }

        role = iam.Role(self,
                 id="li10_network_switch_role",
                 description="role used to turn on and off network components",
                 role_name="li10_network_switch_role",
                 inline_policies=inline_policies,
                 assumed_by= iam.CompositePrincipal(
                     iam.ServicePrincipal("lambda.amazonaws.com"))
        )

        li10_network_switch_lambda = _lambda.Function(self,
            id="li10_network_switch",
            description="Li10 Network Switch",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="main.lambda_handler",
            code=_lambda.Code.from_asset("lambda-code"),
            timeout=Duration.seconds(lambda_timeout),
            role=role,
            log_group=log_group
            )

        # switch on / off
        _rule = events.Rule(self,
            id="OnOff",
            description=event_detail_type,
            event_pattern=events.EventPattern(
                detail=event_detail_trigger,
                detail_type=[event_detail_type],
            )
        ).add_target(targets.LambdaFunction(handler=li10_network_switch_lambda))

        ### For example of how to trigger integrate with the Li10 Switch
        ### please see the Li10 Governance project: https://github.com/li10labs/li10-governance
