import os
import pickle
import logging
from datetime import datetime, timedelta
from typing import List, Dict

from dotenv import load_dotenv
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

import smtplib
from email.mime.text import MIMEText

from datetime import datetime, timedelta, timezone

from summarize import summarize_emails  # your summarizer

# load config 
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# set up Gmail API credentials and scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pickle"
TARGET_EMAIL = os.getenv("TARGET_EMAIL")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
TARGET_EMAIL = os.getenv("TARGET_EMAIL")

if not TARGET_EMAIL:
    logging.error("TARGET_EMAIL not set in .env. Exiting.")
    exit(1)


def get_gmail_service():
    logging.info("Authorizing Gmail API client...")
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)
    service = build("gmail", "v1", credentials=creds)
    logging.info("Gmail API client ready.")
    return service


def fetch_unread_messages(service) -> List[Dict[str, str]]:
    logging.info("Fetching unread messages from the last 31 days...")
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=31)).date()
    # date formatting
    query_date_str = cutoff_date.strftime('%Y/%m/%d')
    query = f"is:unread after:{query_date_str}"
    logging.info(f"Using query: {query}")
    try:
        resp = service.users().messages().list(userId="me", q=query).execute()
        msgs = resp.get("messages", [])
        logging.info(f"Found {len(msgs)} unread message(s).")
        result = []
        for m in msgs:
            data = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in data["payload"]["headers"]}
            snippet = data.get("snippet", "").replace("\n", " ")
            result.append(
                {
                    "id": m["id"],
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "snippet": snippet,
                }
            )
        return result
    except Exception as e:
        logging.error(f"Error fetching unread messages: {e}")
        return []


def mark_as_read(service, msg_id: str):
    logging.info(f"Marking message {msg_id} as read...")
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        logging.error(f"Failed to mark {msg_id} as read: {e}")


def send_email(summary: str):
    logging.info("Sending digest email...")
    msg = MIMEText(summary)
    msg["Subject"] = f"📬 Daily Email Digest ({datetime.now(timezone.utc).date().isoformat()})"
    msg["From"] = TARGET_EMAIL
    msg["To"] = TARGET_EMAIL

    try:
        with smtplib.SMTP("localhost") as server:
            server.send_message(msg)
        logging.info("Digest email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def send_email_via_gmail(service, recipient: str, body: str):
    import base64
    """
    Sends a plain‑text email using the Gmail API.
    service: your authorized Gmail service
    recipient: the address to send to
    body: the plain‑text content of the email
    """
    # Build the MIME message
    msg = MIMEText(body)
    msg["to"] = recipient
    msg["from"] = "me"
    msg["subject"] = f"📬 Daily Digest — {datetime.now(timezone.utc).date().isoformat()}"

    # Gmail API requires base64url‑encoded string
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    message = {"raw": raw}

    sent = service.users().messages().send(userId="me", body=message).execute()
    logging.info(f"Sent message ID {sent['id']} via Gmail API.")
    
def main():
    service = get_gmail_service()
    emails = fetch_unread_messages(service)
    if not emails:
        logging.info("No unread messages to process.")
        return

    # snippets for summarization
    snippets = [
        f"{e['subject']} – {e['snippet']}" for e in emails
    ]
    logging.info("Generating summary of unread emails...")
    try:
        summary = summarize_emails(snippets)
    except Exception as e:
        logging.error(f"Summarization failed: {e}")
        return

    logging.info(f"Summary generated:\n{summary}")
    # send_email(summary) -> TODO: use SMTP isntead of Gmail API
    send_email_via_gmail(service, TARGET_EMAIL, summary)

    for e in emails:
        mark_as_read(service, e["id"])

    logging.info("All done. Exiting.")


if __name__ == "__main__":
    main()
