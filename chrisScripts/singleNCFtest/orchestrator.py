import boto3
import os
import subprocess
import time
import argparse

def run_cmd(cmd, check=True):
    print(f"Running: {cmd}")
    return subprocess.run(cmd, shell=True, check=check)

def ensure_s3_vpc_endpoint(ec2_client, vpc_id, route_table_id):
    print(f"Checking for S3 Gateway VPC Endpoint in VPC {vpc_id}...")
    endpoints = ec2_client.describe_vpc_endpoints(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc_id]},
            {'Name': 'service-name', 'Values': ['com.amazonaws.us-east-2.s3']}
        ]
    )
    if endpoints['VpcEndpoints']:
        vpce_id = endpoints['VpcEndpoints'][0]['VpcEndpointId']
        print(f"S3 VPC Endpoint already exists: {vpce_id}")
        route_tables = endpoints['VpcEndpoints'][0]['RouteTableIds']
        if route_table_id not in route_tables:
            print(f"Associating route table {route_table_id} with VPC endpoint {vpce_id}...")
            ec2_client.modify_vpc_endpoint(
                VpcEndpointId=vpce_id,
                AddRouteTableIds=[route_table_id]
            )
        return vpce_id

    print(f"Creating new S3 Gateway VPC Endpoint for VPC {vpc_id}...")
    response = ec2_client.create_vpc_endpoint(
        VpcId=vpc_id,
        ServiceName='com.amazonaws.us-east-2.s3',
        VpcEndpointType='Gateway',
        RouteTableIds=[route_table_id]
    )
    vpce_id = response['VpcEndpoint']['VpcEndpointId']
    print(f"Created S3 VPC Endpoint: {vpce_id}")
    return vpce_id

def wait_for_ssh(ip, key_path):
    print(f"Waiting for SSH on {ip} to become available...")
    for _ in range(30):
        res = subprocess.run(f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i {key_path} ec2-user@{ip} 'echo SSH_READY'", shell=True, capture_output=True, text=True)
        if "SSH_READY" in res.stdout:
            print("SSH is ready!")
            return True
        time.sleep(5)
    raise Exception("SSH never became available.")

def main():
    parser = argparse.ArgumentParser(description="Automate EC2 spin-up for EarthScope local downloads.")
    parser.add_argument("--inspect", action="store_true", help="Leave the EC2 instance running for inspection instead of terminating it.")
    args = parser.parse_args()

    REGION = 'us-east-2'
    KEY_NAME = 'atos-orchestrator-key'
    KEY_PATH = os.path.expanduser(f'~/.ssh/{KEY_NAME}.pem')
    SG_NAME = 'atos-orchestrator-sg'

    # Initialize AWS EC2 client
    print(f"Connecting to AWS in region {REGION}...")
    ec2 = boto3.client('ec2', region_name=REGION)
    ec2_resource = boto3.resource('ec2', region_name=REGION)

    # 1. Create SSH Key if it doesn't exist
    try:
        ec2.describe_key_pairs(KeyNames=[KEY_NAME])
        print(f"Key pair {KEY_NAME} already exists.")
    except Exception:
        print(f"Creating new key pair {KEY_NAME}...")
        key_pair = ec2.create_key_pair(KeyName=KEY_NAME)
        os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
        with open(KEY_PATH, 'w') as f:
            f.write(key_pair['KeyMaterial'])
        os.chmod(KEY_PATH, 0o400)
        print(f"Saved private key to {KEY_PATH}")

    # 2. Create Security Group
    sg_id = None
    vpc_id = None
    try:
        response = ec2.describe_security_groups(GroupNames=[SG_NAME])
        sg_id = response['SecurityGroups'][0]['GroupId']
        vpc_id = response['SecurityGroups'][0]['VpcId']
        print(f"Security Group {SG_NAME} already exists ({sg_id}).")
    except Exception:
        print(f"Creating Security Group {SG_NAME}...")
        response = ec2.describe_vpcs()
        vpc_id = response['Vpcs'][0]['VpcId']
        sg_response = ec2.create_security_group(GroupName=SG_NAME, Description='Allow SSH for ATOS Orchestrator', VpcId=vpc_id)
        sg_id = sg_response['GroupId']
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[
            {'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
        ])
        print(f"Created Security Group {sg_id} allowing Port 22.")
        


    # 3. Launch EC2 Instance (Amazon Linux 2023)
    AMI_ID = 'ami-0ed003ef4d5dc91e1'
    print("Launching EC2 t3.small instance...")
    instances = ec2_resource.create_instances(
        ImageId=AMI_ID,
        MinCount=1,
        MaxCount=1,
        InstanceType='t3.small',
        KeyName=KEY_NAME,
        SecurityGroupIds=[sg_id],
        TagSpecifications=[{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': 'ATOS-Orchestrator-Download-Node'}]}]
    )
    instance = instances[0]
    print(f"Instance {instance.id} created. Waiting for it to start...")
    instance.wait_until_running()
    instance.reload()
    public_ip = instance.public_ip_address
    print(f"Instance is RUNNING at Public IP: {public_ip}")

    # 4. Wait for SSH and Provision
    wait_for_ssh(public_ip, KEY_PATH)
    
    ssh_base = f"ssh -o StrictHostKeyChecking=no -i {KEY_PATH} ec2-user@{public_ip}"
    scp_base = f"scp -o StrictHostKeyChecking=no -i {KEY_PATH}"

    print("\n--- Provisioning EC2 Server ---")
    run_cmd(f"{ssh_base} 'sudo yum update -y && sudo yum install -y docker rsync && sudo service docker start && sudo usermod -a -G docker ec2-user'")
    
    print("\n--- Transferring Data & Identity ---")
    run_cmd(f"{ssh_base} 'mkdir -p /home/ec2-user/.earthscope /home/ec2-user/wavenet-epicAI'")
    run_cmd(f"{scp_base} -r ~/.earthscope/default ec2-user@{public_ip}:/home/ec2-user/.earthscope/")
    
    # We use rsync to quickly copy the codebase and the generated key index without .git
    run_cmd(f"rsync -avz -e 'ssh -o StrictHostKeyChecking=no -i {KEY_PATH}' --exclude='.git' ~/wavenet-epicAI/ ec2-user@{public_ip}:/home/ec2-user/wavenet-epicAI/")

    print("\n--- Executing Docker Pipeline on AWS ---")
    docker_cmd = (
        "docker run --rm "
        "-v /home/ec2-user/.earthscope:/home/jovyan/.earthscope "
        "-v /home/ec2-user/wavenet-epicAI:/home/jovyan/work "
        "urseismogate.earth.rochester.edu/chrisscripts:latest "
        "python chrisScripts/singleNCFtest/download_pairs.py "
        "--pairs chrisScripts/singleNCFtest/test_pairs.csv "
        "--keyindex chrisScripts/singleNCFtest/keys_partitioned_year "
        "--outdir chrisScripts/singleNCFtest/raw_data "
        "--limit-files 4"
    )
    # The image is on the private registry, so we must tell EC2 to pull it
    run_cmd(f"{ssh_base} 'docker pull urseismogate.earth.rochester.edu/chrisscripts:latest'")
    print("Running download_pairs.py inside EC2...")
    run_cmd(f"{ssh_base} '{docker_cmd}'")

    print("\n--- Retrieving Downloaded Data ---")
    run_cmd(f"{scp_base} -r ec2-user@{public_ip}:/home/ec2-user/wavenet-epicAI/chrisScripts/singleNCFtest/raw_data ~/wavenet-epicAI/chrisScripts/singleNCFtest/")

    # 5. Cleanup
    if args.inspect:
        print(f"\n[INSPECT MODE] Leaving instance {instance.id} running. Don't forget to terminate it later!")
    else:
        print("\n[AUTO-TERMINATION] Pausing for 3 minutes so you can inspect the AWS Console...")
        time.sleep(180)
        print(f"Terminating instance {instance.id}...")
        instance.terminate()
        instance.wait_until_terminated()
        print("Instance terminated successfully to prevent billing.")
        
    print("\nOrchestrator run complete!")

if __name__ == "__main__":
    main()
