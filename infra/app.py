#!/usr/bin/env python3
import os

import aws_cdk as cdk

from cdk.li10_network_switch_stack import Li10NetworkSwitchStack


app = cdk.App()
Li10NetworkSwitchStack(app, "Li10NetworkSwitchStack",
    env=cdk.Environment(
        account="111122223333",
        region='eu-west-3'
    ),
)

app.synth()
