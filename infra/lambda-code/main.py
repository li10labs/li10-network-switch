import boto3
import logging
import json
from datetime import datetime
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_nat_gateway_ids_by_name(ec2_client, name):
    logger.debug(f"Describing NAT Gateways with tag Name={name}")
    response = ec2_client.describe_nat_gateways(Filters=[{'Name': 'tag:Name', 'Values': [name]}])
    nat_gateways = response.get('NatGateways', [])
    return [(nat_gateway['NatGatewayId'], nat_gateway['State']) for nat_gateway in nat_gateways]

def get_vpc_id_from_subnet_id(ec2_client, subnet_id):
    logger.debug(f"Describing subnets with SubnetId={subnet_id}")
    response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
    subnets = response.get('Subnets', [])
    if not subnets:
        raise ValueError(f'Subnet ID {subnet_id} not found')
    return subnets[0]['VpcId']

def create_elastic_ip(ec2_client, name):
    logger.debug("Allocating Elastic IP address")
    response = ec2_client.allocate_address(Domain='vpc')
    allocation_id = response['AllocationId']
    creation_date = datetime.utcnow().isoformat()
    ec2_client.create_tags(Resources=[allocation_id], Tags=[
        {'Key': 'Name', 'Value': name},
        {'Key': 'li10-nat-switch', 'Value': creation_date}
    ])
    logger.info(f'Elastic IP created with Allocation ID {allocation_id} and Name {name}')
    return allocation_id

def release_elastic_ip(ec2_client, allocation_id):
    logger.debug(f"Releasing Elastic IP address with AllocationId={allocation_id}")
    try:
        ec2_client.release_address(AllocationId=allocation_id)
        logger.info(f'Elastic IP with Allocation ID {allocation_id} released')
    except ClientError as e:
        logger.error(f'Error releasing Elastic IP {allocation_id}: {e}')

def delete_elastic_ips_by_name(ec2_client, name):
    logger.debug(f"Describing Elastic IPs with tag Name={name}")
    response = ec2_client.describe_addresses(Filters=[{'Name': 'tag:Name', 'Values': [name]}])
    for address in response['Addresses']:
        allocation_id = address['AllocationId']
        logger.debug(f"Releasing Elastic IP with AllocationId={allocation_id}")
        release_elastic_ip(ec2_client, allocation_id)

def create_nat_gateway(ec2_client, subnet_id, name):
    existing_nats = get_nat_gateway_ids_by_name(ec2_client, name)
    for nat_id, state in existing_nats:
        if state in ['available', 'pending']:
            logger.info(f'NAT Gateway with name {name} already exists with ID {nat_id} and is in state {state}. Skipping creation.')
            return nat_id, None

    vpc_id = get_vpc_id_from_subnet_id(ec2_client, subnet_id)
    allocation_id = create_elastic_ip(ec2_client, name)

    logger.debug(f"Creating NAT Gateway in SubnetId={subnet_id} with AllocationId={allocation_id}")
    response = ec2_client.create_nat_gateway(SubnetId=subnet_id, AllocationId=allocation_id)
    nat_gateway_id = response['NatGateway']['NatGatewayId']
    creation_date = datetime.utcnow().isoformat()

    logger.debug(f"Creating tags for NAT Gateway {nat_gateway_id}")
    ec2_client.create_tags(Resources=[nat_gateway_id], Tags=[
        {'Key': 'Name', 'Value': name},
        {'Key': 'li10-nat-switch', 'Value': creation_date}
    ])
    logger.info(f'NAT Gateway {name} created with ID {nat_gateway_id}')

    # Wait until the NAT Gateway is available
    waiter = ec2_client.get_waiter('nat_gateway_available')
    logger.info(f'Waiting for NAT Gateway {nat_gateway_id} to become available...')
    waiter.wait(NatGatewayIds=[nat_gateway_id])
    logger.info(f'NAT Gateway {nat_gateway_id} is now available')

    # Update blackhole routes in the VPC
    update_blackhole_routes(ec2_client, vpc_id, nat_gateway_id)

    return nat_gateway_id, allocation_id

def delete_nat_gateways(ec2_client, nat_gateway_ids, name):
    for nat_gateway_id, state in nat_gateway_ids:
        if state != 'available':
            continue

        logger.debug(f"Describing NAT Gateway with NatGatewayId={nat_gateway_id}")
        try:
            response = ec2_client.describe_nat_gateways(NatGatewayIds=[nat_gateway_id])
            if not response['NatGateways']:
                logger.info(f'NAT Gateway {nat_gateway_id} does not exist or is already deleted. Skipping deletion.')
                continue
            allocation_id = response['NatGateways'][0]['NatGatewayAddresses'][0]['AllocationId']

            logger.debug(f"Deleting NAT Gateway with NatGatewayId={nat_gateway_id}")
            ec2_client.delete_nat_gateway(NatGatewayId=nat_gateway_id)
            logger.info(f'NAT Gateway {nat_gateway_id} deleted')

            # Wait until the NAT Gateway is deleted
            waiter = ec2_client.get_waiter('nat_gateway_deleted')
            logger.info(f'Waiting for NAT Gateway {nat_gateway_id} to be deleted...')
            waiter.wait(NatGatewayIds=[nat_gateway_id])
            logger.info(f'NAT Gateway {nat_gateway_id} has been deleted')

            # Release the Elastic IP
            release_elastic_ip(ec2_client, allocation_id)
        except ClientError as e:
            logger.error(f'Error deleting NAT Gateway {nat_gateway_id}: {e}')

    # Delete all Elastic IPs with the given name
    delete_elastic_ips_by_name(ec2_client, name)

def update_blackhole_routes(ec2_client, vpc_id, nat_gateway_id):
    logger.debug(f"Describing route tables for VpcId={vpc_id}")
    try:
        route_tables = ec2_client.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['RouteTables']
        for route_table in route_tables:
            for route in route_table['Routes']:
                if route.get('State') == 'blackhole' and route.get('NatGatewayId'):
                    logger.debug(f"Replacing blackhole route {route['DestinationCidrBlock']} in RouteTableId={route_table['RouteTableId']} with NatGatewayId={nat_gateway_id}")
                    ec2_client.replace_route(
                        RouteTableId=route_table['RouteTableId'],
                        DestinationCidrBlock=route['DestinationCidrBlock'],
                        NatGatewayId=nat_gateway_id
                    )
                    logger.info(f'Replaced blackhole route {route["DestinationCidrBlock"]} in route table {route_table["RouteTableId"]} with NAT Gateway {nat_gateway_id}')
    except ClientError as e:
        logger.error(f'Error updating blackhole routes: {e}')

def create_follow_up_event(event_detail):
    logger.debug(f"Creating follow-up event: {event_detail}")
    eventbridge_client = boto3.client('events')
    response = eventbridge_client.put_events(
        Entries=[
            {
                'Source': event_detail['source'],
                'DetailType': event_detail['action'],
                'Detail': json.dumps(event_detail),
                'EventBusName': 'default'
            }
        ]
    )
    logger.info(f"Follow-up event created: {response}")

def handle_event(event, context):
    action = event['detail']['action']
    name = event['detail']['name']
    region = event.get('region')
    subnet_id = event['detail'].get('subnet_id')
    profile = None

    session = boto3.Session(region_name=region) if region else boto3.Session()
    ec2_client = session.client('ec2')

    if action == 'create':
        if not subnet_id:
            logger.error('Subnet ID is required for creating a NAT Gateway')
            return
        create_nat_gateway(ec2_client, subnet_id, name)

    elif action == 'delete':
        nat_gateway_ids = get_nat_gateway_ids_by_name(ec2_client, name)
        if not nat_gateway_ids:
            logger.info(f'NAT Gateway with name {name} not found or is already deleted. Skipping deletion.')
            return
        delete_nat_gateways(ec2_client, nat_gateway_ids, name)

    else:
        logger.error(f'Invalid action: {action}. Use "create" or "delete".')
        return

    # Handle follow-up event
    if 'follow-up-event' in event['detail']:
        create_follow_up_event(event['detail']['follow-up-event'])

def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    logger.info(f"Context: {context}")

    try:
        handle_event(event, context)
    except:
        logger.error(f'handle_event failed')


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Add or remove an AWS NAT Gateway from a VPC')
    parser.add_argument('action', choices=['create', 'delete'], help='Action to perform')
    parser.add_argument('name', help='Name of the NAT Gateway')
    parser.add_argument('--subnet-id', help='Subnet ID for creating NAT Gateway')
    parser.add_argument('--profile', help='AWS profile to use')
    parser.add_argument('--region', help='AWS region to use')

    args = parser.parse_args()

    event = {
        'detail': {
            'action': args.action,
            'name': args.name,
            'subnet_id': args.subnet_id,
        },
        'region': args.region
    }

    handle_event(event, None)

if __name__ == '__main__':
    main()
