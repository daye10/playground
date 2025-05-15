# AWS Image Processing Web Application

This project is a web-based image-processing tool that automatically resizes uploaded images into multiple formats. It demonstrates a cloud-native architecture leveraging several AWS services for scalability, asynchronous processing, and efficient data management.

Users can upload images through a simple web interface. The original image is stored in an AWS S3 bucket. An AWS Lambda function is then automatically triggered to process the uploaded image, resizing it into predefined formats (e.g., thumbnail, medium, large). These resized images are stored in a separate S3 bucket (or a different prefix). Metadata about the images, including their S3 locations, processing status, and timestamps, is stored in an AWS DynamoDB table for fast, indexed retrieval, enabling the web application to display the status and links to the processed images.

The web application itself is a Python Flask application designed to be hosted on an AWS EC2 instance.

## Architecture Diagram / Workflow

1.  **User Upload (Client -> EC2 Flask App)**:
    * A user accesses the web application (hosted on EC2) via their browser.
    * The user selects an image and uploads it through an HTML form.

2.  **Initial Handling (EC2 Flask App -> S3 & DynamoDB)**:
    * The Flask backend receives the image.
    * It generates a unique `ImageID` for tracking.
    * The original image is uploaded to a designated S3 bucket (e.g., `originals-bucket`). The `ImageID` is stored as S3 object metadata (`x-amz-meta-image-id`).
    * An initial metadata record (including `ImageID`, S3 key, original filename, `UploadTimestamp`, and `ProcessingStatus: PENDING`) is created in a DynamoDB table.

3.  **Asynchronous Processing (S3 Event -> Lambda)**:
    * The new object creation in the "originals" S3 bucket automatically triggers an AWS Lambda function.

4.  **Image Resizing (Lambda -> Pillow -> S3 & DynamoDB)**:
    * The Lambda function retrieves the `ImageID` from the S3 object's metadata.
    * It updates the DynamoDB item's status to `PROCESSING`.
    * It downloads the original image from the "originals" S3 bucket.
    * Using the Pillow (PIL) library, it resizes the image into multiple predefined formats/sizes.
    * The resized images are uploaded to a different S3 bucket or prefix (e.g., `resized-bucket`).
    * The Lambda function updates the DynamoDB item for that `ImageID` with `ProcessingStatus: COMPLETED` (or `FAILED`) and adds the S3 URIs or identifiers for the resized images.

5.  **Displaying Results (EC2 Flask App & DynamoDB -> Client)**:
    * The Flask web application provides an API endpoint (`/api/images`).
    * The frontend JavaScript calls this API to fetch the list of images and their metadata (including processing status and links to resized images, potentially using S3 presigned URLs for access).
    * The webpage dynamically displays the gallery of uploaded images, their status, and links to the different versions.

## Technologies Used

* **AWS Services**:
    * **Amazon EC2 (Elastic Compute Cloud)**: Hosts the Flask web application.
    * **Amazon S3 (Simple Storage Service)**: Stores original and resized images.
    * **AWS Lambda**: Performs asynchronous serverless image resizing.
    * **Amazon DynamoDB**: NoSQL database for storing image metadata for fast retrieval.
    * **AWS IAM (Identity and Access Management)**: Manages permissions for AWS services.
    * **Amazon CloudWatch Logs**: Collects logs from Lambda and potentially EC2.
* **Development Tools**:
    * Git & GitHub (for version control)
    * Docker (recommended for creating AWS Lambda deployment packages)
    * AWS CLI (Command Line Interface)

## Prerequisites


1.  **An AWS Account**
2.  **AWS CLI**
    * Run `aws configure` and set up your credentials (for an IAM User, not root) and default region.
3.  **Python 3.8+**
4.  **Docker Desktop** 

## Detailed Setup and Deployment Instructions

**Phase I: AWS Account & Initial Security**

1.  **Create/Verify AWS Account**: Ensure you have an active AWS account.
2.  **Create an IAM User**:
    * Log in to the AWS Console as the **root user**.
    * Navigate to the **IAM** service.
    * Create a new user (e.g., `your-project-admin`).
    * Grant this user `AdministratorAccess` (for initial setup and learning; refine to least privilege for production).
    * Generate and securely save the **Access Key ID** and **Secret Access Key**.
    * **Enable Multi-Factor Authentication (MFA)** on both your root account and this new IAM user.
3.  **Configure AWS CLI**:
    * Open your terminal and run `aws configure`.
    * Enter the Access Key ID and Secret Access Key of the IAM user you just created.
    * Set your desired **Default region name** (e.g., `us-east-2`). **IMPORTANT: Use this same region for ALL resources created in this project.**
    * Set Default output format (e.g., `json`).


**Phase II: AWS Resource Creation**

*Ensure you are in your chosen AWS region (e.g., `us-east-2`) in the AWS Management Console for all these steps.*

1.  **Create S3 Buckets:**
    * Navigate to the **S3** service.
    * Create two buckets:
        1.  **Originals Bucket**: e.g., `<your-initials>-image-originals-YYYYMMDD`. Note the exact name.
            * Keep "Block all public access" ON (default).
        2.  **Resized Bucket**: e.g., `<your-initials>-image-resized-YYYYMMDD`. Note the exact name.
            * Keep "Block all public access" ON. Access to resized images can be granted via presigned URLs or CloudFront later.
    * Update `S3_ORIGINALS_BUCKET_NAME` and `S3_RESIZED_BUCKET_NAME` in your `.env` file.

2.  **Create DynamoDB Table:**
    * Navigate to the **DynamoDB** service.
    * Click "Create table".
    * **Table name**: e.g., `ImageProcessingMetadata`. Note the exact name.
    * **Primary key (Partition key)**: `ImageID` (Type: `String`).
    * Use default settings (which usually means "On-demand" capacity mode).
    * Click "Create table".
    * Update `DYNAMODB_TABLE_NAME` in your `.env` file.

**Phase III: Running the Flask Web Application Locally**

1.  **Verify Configuration:** Double-check your `.env` file for correct bucket names, table name, and region.
2.  **Run the Flask App:**
    ```bash
    flask run
    ```
3.  **Test Locally:**
    * Open your browser to `http://127.0.0.1:5000` (or the address shown in the terminal).
    * Try uploading an image.
    * **Check:**
        * The Flask server console for logs (successful S3 upload, successful DynamoDB `PutItem`).
        * Your "originals" S3 bucket in the AWS Console to see the uploaded file.
        * Your DynamoDB table in the AWS Console to see the new metadata item with `ProcessingStatus: PENDING`.
        * The "Uploaded Images" section on your webpage should update (it will initially show "PENDING" or try to fetch from `/api/images`).

**Phase IV: AWS Lambda Function for Image Resizing**

1.  **Create Lambda Deployment Package (Using Docker - Recommended):**
    * Ensure Docker Desktop is running.
    * Create a `Dockerfile` inside the `lambda_function` directory with the following content:
        ```dockerfile
        # official AWS Lambda Python base image matching your chosen runtime
        FROM public.ecr.aws/lambda/python:3.9

        WORKDIR ${LAMBDA_TASK_ROOT}

        COPY requirements.txt .

        # Install dependencies
        RUN pip install -r requirements.txt -t .

        COPY lambda_function.py .

        # Command can be an empty array as Lambda invokes the handler directly
        CMD []
        ```
    * Navigate your terminal to the `image-processor-aws/lambda_function/` directory.
    * Build and extract the package:
        ```bash
        docker build -t lambda-image-packager .

        #  dummy container from the image
        docker create --name dummy-packager lambda-image-packager

        # temp dir for the package contents if it doesn't exist
        mkdir -p ./package_contents

        docker cp dummy-packager:/var/task/. ./package_contents/

        docker rm dummy-packager

        cd ./package_contents
        zip -r ../lambda_deployment_package.zip .
        cd ..

        #  Clean up the temporary directory
        # rm -rf ./package_contents
        ```
        You will now have `lambda_deployment_package.zip` in the `lambda_function` directory.

3.  **Create Lambda Function in AWS Console (in the same region, e.g., `us-east-2`):**
    * Navigate to the **Lambda** service.
    * Click **"Create function"**.
    * **Author from scratch**.
    * **Function name**: e.g., `ImageResizerFunction`.
    * **Runtime**: Python 3.9 (or your chosen Python 3.x version).
    * **Architecture**: `x86_64`.
    * **Permissions**: Choose **"Create a new role with basic Lambda permissions"**. Note the role name.
    * Click **"Create function"**.

4.  **Configure Lambda Function:**
    * **Code source**: Upload the `lambda_deployment_package.zip` file.
    * **Handler (Runtime settings)**: `lambda_function.lambda_handler`.
    * **Environment variables** (Configuration -> Environment variables -> Edit):
        * `S3_RESIZED_BUCKET_NAME`: `<YOUR_UNIQUE_S3_RESIZED_BUCKET_NAME>`
        * `DYNAMODB_TABLE_NAME`: `<YOUR_DYNAMODB_TABLE_NAME>`
    * **Basic settings** (General configuration -> Edit):
        * **Memory**: Start with `256 MB` or `512 MB`.
        * **Timeout**: Start with `30 seconds` or `1 minute`.
    * **IAM Role Permissions**:
        * Go to Configuration -> Permissions. Click on the Role name.
        * In the IAM console, click "Add permissions" -> "Create inline policy".
        * Select JSON and paste the policy (replace placeholders `<YOUR_ACCOUNT_ID>`, `<REGION>`, bucket names, table name, and your Lambda function name):
            ```json
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents"
                        ],
                        "Resource": "arn:aws:logs:<REGION>:<YOUR_ACCOUNT_ID>:log-group:/aws/lambda/<YOUR_LAMBDA_FUNCTION_NAME>:*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:GetObjectTagging"
                        ],
                        "Resource": "arn:aws:s3:::<YOUR_UNIQUE_S3_ORIGINALS_BUCKET_NAME>/*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": "s3:PutObject",
                        "Resource": "arn:aws:s3:::<YOUR_UNIQUE_S3_RESIZED_BUCKET_NAME>/*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:Query",
                            "dynamodb:Scan"
                        ],
                        "Resource": "arn:aws:dynamodb:<REGION>:<YOUR_ACCOUNT_ID>:table/<YOUR_DYNAMODB_TABLE_NAME>"
                    }
                ]
            }
            ```
        * Name the policy (e.g., `ImageResizerLambdaPermissions`) and create it.

5.  **Add S3 Trigger to Lambda:**
    * In the Lambda function's "Function overview", click "+ Add trigger".
    * Select **S3**.
    * **Bucket**: Choose your "originals" S3 bucket.
    * **Event type**: "All object create events".
    * Acknowledge recursive invocation warning.
    * Click "Add".

**Phase V: Testing the Full End-to-End Flow**

1.  Upload an image using your local Flask web application.
2.  **Monitor:**
    * **Flask App Logs**: Successful S3 upload & initial DynamoDB write.
    * **S3 Originals Bucket**: Original image appears. Check its metadata for `x-amz-meta-image-id`.
    * **Lambda CloudWatch Logs**:
        * Go to Lambda -> Your Function -> Monitor -> "View logs in CloudWatch".
        * Check the latest log stream for execution details and any errors from `lambda_function.py`.
    * **S3 Resized Bucket**: Resized images should appear after a few moments.
    * **DynamoDB Table**: The item's `ProcessingStatus` should change to `PROCESSING`, then `COMPLETED` (or `FAILED`), and `ResizedUrls` should be populated.
    * **Web Application**: Refresh the image gallery. It should show the updated status and links/previews to resized images.

**Phase VI: Deploying Flask App to EC2**


1.  **Launch an EC2 Instance**: Choose an Amazon Linux 2 or Ubuntu AMI. Select an instance type (e.g., `t2.micro` or `t3.micro` - Free Tier eligible).
2.  **IAM Role for EC2**: Create an IAM Role for EC2 that has permissions to:
    * Read/Write to your S3 buckets (originals and resized).
    * Read/Write to your DynamoDB table.
    * (Optional) Write logs to CloudWatch Logs.
    * Attach this role to your EC2 instance during or after launch. **This is more secure than storing AWS credentials on the instance.**
3.  **Configure Security Group**: Allow inbound traffic on:
    * Port `22` (SSH - from your IP only).
    * Port `80` (HTTP - from anywhere, or your IP for testing).
    * (Optional) Port `443` (HTTPS - if you set up SSL).
4.  **Connect to EC2 (SSH)**.
5.  **Install Dependencies on EC2**: Python, Pip, Git, Nginx (as a reverse proxy), Gunicorn (as a WSGI server for Flask).
6.  **Clone Your Code** onto the EC2 instance.
7.  **Set up Environment Variables on EC2**: Instead of a `.env` file with AWS keys, your application will use the attached IAM Role. You'll still need to set application-specific environment variables (like bucket names, table name, Flask secret key) for Gunicorn/your application to use. These can be set in systemd service files or shell profiles.
8.  **Configure Gunicorn** to serve your Flask app.
9.  **Configure Nginx** as a reverse proxy to forward requests to Gunicorn.
10. **Start Services** and test.
