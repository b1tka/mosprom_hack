"""Клиент S3 (boto3) для админки."""
from functools import lru_cache

import boto3
from botocore.config import Config

from adminapp import config


@lru_cache(maxsize=1)
def s3():
    return boto3.client(
        "s3", endpoint_url=config.S3_ENDPOINT, aws_access_key_id=config.S3_ACCESS_KEY, aws_secret_access_key=config.S3_SECRET_KEY,
        region_name="us-east-1", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=10, retries={"max_attempts": 1}),
    )
