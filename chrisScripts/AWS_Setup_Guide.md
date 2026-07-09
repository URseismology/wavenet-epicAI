# AWS Initial Setup & Authentication Guide

Before you can run the EarthScope Zero-Egress Cloud Pipeline, you must have your own AWS account and configure your local machine with the correct credentials. 

This guide walks you through creating an account, generating security keys, installing the AWS CLI, and linking your Mac/PC to AWS.

---

## 1. Create an AWS Account
1. Go to the [AWS Free Tier Sign-Up Page](https://aws.amazon.com/free/).
2. Click **Create a Free Account** and follow the prompts. You will need to provide a credit card, but the tiny `t3.micro` EC2 instances we use for testing cost fractions of a penny (and are often covered completely by the free tier).
3. Once registered, log into the [AWS Management Console](https://console.aws.amazon.com/) using your root email address.

## 2. Create an IAM User (Crucial for Security)
Do **not** use your Root Account credentials to run scripts. Instead, we will create a dedicated programmatic user.

1. In the AWS Console search bar at the top, type **IAM** and click the IAM service.
2. On the left sidebar, click **Users** -> **Create user**.
3. Name the user `atos-orchestrator` (or similar) and click **Next**.
4. On the Permissions page, select **Attach policies directly**.
5. Search for and check the boxes next to these two exact policies:
    * `AmazonEC2FullAccess`
    * `AmazonS3FullAccess`
6. Click **Next**, then **Create user**.

## 3. Generate Access Keys
Your local Python scripts need cryptographic keys to prove they are acting on behalf of your IAM user.

1. Back on the IAM **Users** page, click your newly created `atos-orchestrator` user.
2. Click the **Security credentials** tab.
3. Scroll down to **Access keys** and click **Create access key**.
4. Select **Command Line Interface (CLI)**, check the confirmation box, and click **Next** -> **Create access key**.
5. **CRITICAL:** You will see an **Access key ID** and a **Secret access key**. This is the *only* time AWS will ever show you the Secret key. Leave this browser tab open, or download the `.csv` file.

## 4. Install the AWS CLI
You must install the official AWS Command Line Interface on your local machine (your Mac/PC).

* **Mac:** Run `brew install awscli` (or download the `.pkg` from [AWS](https://aws.amazon.com/cli/)).
* **Windows/Linux:** Follow the official installation instructions [here](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).

To verify the installation, open your terminal and run:
```bash
aws --version
```

## 5. Configure Your Local Machine
Now we link the AWS CLI to your IAM User keys.

1. Open your terminal and run:
   ```bash
   aws configure
   ```
2. The terminal will prompt you for four pieces of information. Enter them exactly as follows:
   * **AWS Access Key ID:** (Paste the Access key ID from Step 3)
   * **AWS Secret Access Key:** (Paste the Secret access key from Step 3)
   * **Default region name:** `us-east-2`
     * *🛑 CRITICAL: You MUST type exactly `us-east-2`. The EarthScope open data buckets live here. If you use any other region, EarthScope will block your downloads with an `FgaAccessDenied` error to prevent massive cross-region egress fees.*
   * **Default output format:** `json`

## 6. Next Steps
Your local machine is now fully authorized to launch EC2 instances and interact with S3. 

You are now ready to proceed to the orchestration phase! 
👉 **Please see the [AWS Docker Pipeline Guide](./AWS_Docker_Pipeline_Guide.md) for instructions on authenticating with EarthScope and running the automated test suite.**
