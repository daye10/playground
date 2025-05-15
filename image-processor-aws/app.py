import os
import uuid
from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError
import logging
from datetime import datetime, timezone

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'a_default_secret_key_if_not_set')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AWS Clients (configure once)
# for local development, Boto3 will use credentials from ~/.aws/credentials or environment variables
# for EC2, it will use the IAM role attached to the instance
try:
    s3_client = boto3.client('s3', region_name=os.getenv('AWS_DEFAULT_REGION'))
    dynamodb_client = boto3.client('dynamodb', region_name=os.getenv('AWS_DEFAULT_REGION'))
except Exception as e:
    logger.error(f"Error initializing AWS clients: {e}")
    s3_client = None
    dynamodb_client = None

S3_ORIGINALS_BUCKET = os.getenv('S3_ORIGINALS_BUCKET_NAME')
DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE_NAME')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html', page_title="Image Uploader")

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file uploads."""
    if not s3_client or not dynamodb_client:
        return jsonify({"error": "AWS clients not initialized. Check server logs."}), 500
    if not S3_ORIGINALS_BUCKET or not DYNAMODB_TABLE:
        return jsonify({"error": "S3 bucket name or DynamoDB table name not configured."}), 500

    if 'imageFile' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['imageFile']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        original_filename = file.filename
        # Create a unique filename for S3 to avoid overwrites
        file_extension = original_filename.rsplit('.', 1)[1].lower()
        unique_s3_filename = f"{uuid.uuid4()}.{file_extension}"
        image_id = str(uuid.uuid4()) 

        try:
            # Upload to S3
            s3_client.upload_fileobj(
                file,
                S3_ORIGINALS_BUCKET,
                unique_s3_filename,
                ExtraArgs={ 
                    'Metadata': {
                        'image-id': image_id, 
                        'original-filename': original_filename
                    }
                }
            )
            logger.info(f"File {unique_s3_filename} uploaded to {S3_ORIGINALS_BUCKET}")

            # Store metadata
            item = {
                'ImageID': {'S': image_id},
                'OriginalFilename': {'S': original_filename},
                'S3ObjectKey': {'S': unique_s3_filename},
                'UploadTimestamp': {'S': datetime.now(timezone.utc).isoformat()},
                'ProcessingStatus': {'S': 'PENDING'},
                'OriginalS3Url': {'S': f"s3://{S3_ORIGINALS_BUCKET}/{unique_s3_filename}"}
            }
            dynamodb_client.put_item(
                TableName=DYNAMODB_TABLE,
                Item=item
            )
            logger.info(f"Metadata for {image_id} stored in DynamoDB.")

            return jsonify({
                "message": "File uploaded successfully, processing started.",
                "imageId": image_id,
                "filename": original_filename,
                "s3_key": unique_s3_filename
            }), 200

        except ClientError as e:
            logger.error(f"AWS ClientError during upload: {e}")
            return jsonify({"error": f"Upload failed: {e.response['Error']['Message']}"}), 500
        except Exception as e:
            logger.error(f"An unexpected error occurred during upload: {e}")
            return jsonify({"error": "An unexpected error occurred. Please try again."}), 500
    else:
        return jsonify({"error": "File type not allowed"}), 400

@app.route('/api/images', methods=['GET'])
def get_images():
    """API endpoint to fetch image metadata."""
    if not dynamodb_client or not DYNAMODB_TABLE:
        return jsonify({"error": "DynamoDB client or table not configured."}), 500
    try:

        response = dynamodb_client.scan(TableName=DYNAMODB_TABLE)
        items = response.get('Items', [])

        processed_items = []
        for item in items:
            processed_item = {key: val.get('S') or val.get('N') for key, val in item.items()}
            if 'S3ObjectKey' in processed_item and S3_ORIGINALS_BUCKET:
                 try:
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': S3_ORIGINALS_BUCKET, 'Key': processed_item['S3ObjectKey']},
                        ExpiresIn=3600  
                    )
                    processed_item['displayUrl'] = presigned_url
                 except Exception as e:
                    logger.warning(f"Could not generate presigned URL for {processed_item['S3ObjectKey']}: {e}")
                    processed_item['displayUrl'] = None


            processed_items.append(processed_item)


        return jsonify(processed_items), 200
    except ClientError as e:
        logger.error(f"Error fetching images from DynamoDB: {e}")
        return jsonify({"error": f"Could not fetch images: {e.response['Error']['Message']}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error fetching images: {e}")
        return jsonify({"error": "An unexpected error occurred."}), 500


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)