import boto3
from earthscope_sdk import EarthScopeClient

print("Authenticating...")
es_client = EarthScopeClient()
creds = es_client.user.get_aws_credentials()

from botocore.config import Config

session = boto3.Session(
    aws_access_key_id=creds.aws_access_key_id,
    aws_secret_access_key=creds.aws_secret_access_key.get_secret_value() if hasattr(creds.aws_secret_access_key, 'get_secret_value') else creds.aws_secret_access_key,
    aws_session_token=creds.aws_session_token.get_secret_value() if hasattr(creds.aws_session_token, 'get_secret_value') else creds.aws_session_token,
    region_name='us-east-2'
)
s3_client = session.client("s3", config=Config(response_checksum_validation='when_required'))

BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"

# 5. Let's list objects in the S3 Access Point to ensure we can see files
print("\nListing objects in", BUCKET, "with prefix miniseed/IU/1988/...")
try:
    response = s3_client.list_objects_v2(Bucket=BUCKET, Prefix="miniseed/IU/1988/001/HRV.IU.1988.001#1")
    if 'Contents' in response:
        keys = [obj['Key'] for obj in response['Contents'][:5]]
        print(f"Found keys: {keys}")
    else:
        print("No objects found with this prefix.")
        exit()
except Exception as e:
    print(f"FAILED to list objects: {e}")
    exit()

# Try fetching a single object to test GetObject and Checksum error
try:
    print(f"\nTrying to get object {keys[0]}...")
    resp = s3_client.get_object(Bucket=BUCKET, Key=keys[0])
    print("SUCCESS! File fetched successfully!", resp['Body'].read(10))
except Exception as e:
    print(f"FAILED: {e}")
