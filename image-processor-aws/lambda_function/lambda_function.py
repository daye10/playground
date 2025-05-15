import os
import io
import json
import logging
from datetime import datetime, timezone

import boto3
from PIL import Image, UnidentifiedImageError


logger = logging.getLogger()
logger.setLevel(logging.INFO)


RESIZED_BUCKET = os.getenv("S3_RESIZED_BUCKET_NAME")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE_NAME")

if not RESIZED_BUCKET or not DYNAMODB_TABLE:
    logger.error("Missing required environment variables S3_RESIZED_BUCKET_NAME or DYNAMODB_TABLE_NAME")
    raise RuntimeError("Configuration error")

# AWS clients
s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")

# (name, max_width, max_height, extension, PIL_format)
TARGET_SIZES = [
    ("thumbnail", 100, 100, "jpg", "JPEG"),
    ("medium",    600, 600, "jpg", "JPEG"),
    ("large",    1200,1200,"webp","WEBP"),
]

def resize_and_upload(image_bytes: bytes, filename_stem: str):
    """Resize the image into several variants and upload each to S3."""
    resized_urls = {}
    all_ok = True

    for name, w, h, ext, fmt in TARGET_SIZES:
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.thumbnail((w, h))
                # Convert RGBA/P to RGB for JPEG
                if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                buffer = io.BytesIO()
                img.save(buffer, format=fmt)
                buffer.seek(0)

                key = f"resized/{filename_stem}_{name}.{ext}"
                content_type = Image.MIME.get(fmt, "application/octet-stream")

                s3.put_object(
                    Bucket=RESIZED_BUCKET,
                    Key=key,
                    Body=buffer,
                    ContentType=content_type,
                )

                url = f"s3://{RESIZED_BUCKET}/{key}"
                resized_urls[name] = url
                logger.info(f"Uploaded {name} → {url}")

        except UnidentifiedImageError:
            logger.error(f"Cannot identify image for variant '{name}'")
            all_ok = False
        except Exception as e:
            logger.error(f"Error resizing/uploading '{name}': {e}")
            all_ok = False

    return resized_urls, all_ok

def update_dynamodb(image_id: str, status: str, resized_urls: dict = None, error_msg: str = None):
    """Write ProcessingStatus, timestamp, and optional data into DynamoDB."""
    ts = datetime.now(timezone.utc).isoformat()
    parts = ["ProcessingStatus = :s", "ProcessingTimestamp = :t"]
    vals = {
        ":s": {"S": status},
        ":t": {"S": ts},
    }

    if resized_urls:
        parts.append("ResizedUrls = :r")
        vals[":r"] = {"M": {k: {"S": v} for k, v in resized_urls.items()}}

    if error_msg:
        parts.append("ErrorMessage = :e")
        vals[":e"] = {"S": error_msg}

    update_expr = "SET " + ", ".join(parts)

    try:
        dynamodb.update_item(
            TableName=DYNAMODB_TABLE,
            Key={"ImageID": {"S": image_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=vals,
        )
        logger.info(f"DynamoDB updated for {image_id} (status={status})")
    except Exception as e:
        logger.error(f"Failed to update DynamoDB for {image_id}: {e}")

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    for record in event.get("Records", []):
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key    = record.get("s3", {}).get("object", {}).get("key")
        if not bucket or not key:
            logger.error("Skipping record missing bucket/key: %s", record)
            continue

        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            image_id = head.get("Metadata", {}).get("image-id")
            if not image_id:
                raise KeyError("x-amz-meta-image-id missing")
        except Exception as e:
            logger.error("Metadata error for %s: %s", key, e)
            continue

        logger.info("Processing ImageID %s (object %s)", image_id, key)
        update_dynamodb(image_id, "PROCESSING")

        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            data = resp["Body"].read()
            stem = os.path.splitext(key)[0]

            resized_map, ok = resize_and_upload(data, stem)
            if ok:
                update_dynamodb(image_id, "COMPLETED", resized_urls=resized_map)
            else:
                update_dynamodb(image_id, "FAILED", error_msg="One or more variants failed")
        except Exception as e:
            logger.error("Error processing %s: %s", key, e)
            update_dynamodb(image_id, "FAILED", error_msg=str(e))

    return {"statusCode": 200, "body": json.dumps("Processing complete")}
