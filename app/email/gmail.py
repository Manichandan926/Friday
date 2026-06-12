import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import settings
from app.core.logger import logger

# Read-only access to list and retrieve inbox messages
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Initializes or refreshes authentication tokens, returning the Gmail service client."""
    creds = None
    token_path = settings.BASE_DIR / "token.json"
    credentials_path = settings.BASE_DIR / "credentials.json"

    # Load stored token if exists
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.error(f"Failed to load token.json: {e}")

    # Re-authenticate if token is missing or invalid
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Gmail OAuth token expired. Attempting refresh...")
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Failed to refresh OAuth token: {e}")
                creds = None
        
        if not creds:
            if not credentials_path.exists():
                logger.warning("credentials.json not found in workspace root. Skipping Gmail sync.")
                return None
            
            logger.info("Initializing Gmail OAuth user flow server...")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0, open_browser=True, success_message="FRIDAY authenticated. Close this tab.")
            except Exception as e:
                logger.error(f"Failed to run OAuth login flow: {e}")
                return None

        # Cache credentials for next background execution
        try:
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
            logger.info("Saved fresh Gmail OAuth token to token.json.")
        except Exception as e:
            logger.error(f"Failed to cache token.json: {e}")

    return build('gmail', 'v1', credentials=creds)

def fetch_unread_emails(service, max_results=10):
    """Retrieves list of message descriptors representing unread inbox emails."""
    try:
        results = service.users().messages().list(
            userId='me', 
            q='is:unread label:INBOX', 
            maxResults=max_results
        ).execute()
        return results.get('messages', [])
    except Exception as e:
        logger.error(f"Failed to fetch unread messages list from Gmail: {e}")
        return []

def get_email_details(service, msg_id: str):
    """Retrieves full text and headers of a specific email, returning flat structure."""
    try:
        message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        payload = message.get('payload', {})
        headers = payload.get('headers', [])
        
        subject = "No Subject"
        sender = "Unknown"
        received_at_str = ""
        
        for h in headers:
            name = h['name'].lower()
            if name == 'subject':
                subject = h['value']
            elif name == 'from':
                sender = h['value']
            elif name == 'date':
                received_at_str = h['value']

        body = ""
        if 'parts' in payload:
            body = _extract_body_recursive(payload['parts'])
        else:
            body_data = payload.get('body', {}).get('data', '')
            if body_data:
                body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')

        return {
            "id": msg_id,
            "subject": subject,
            "sender": sender,
            "body": body,
            "received_at_str": received_at_str
        }
    except Exception as e:
        logger.error(f"Error downloading details for message ID {msg_id}: {e}")
        return None

def _extract_body_recursive(parts) -> str:
    """Recursively checks nested parts to extract plain text body payloads."""
    for part in parts:
        mime = part.get('mimeType', '')
        if mime == 'text/plain':
            body_data = part.get('body', {}).get('data', '')
            if body_data:
                return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
        elif 'parts' in part:
            text = _extract_body_recursive(part['parts'])
            if text:
                return text
    return ""
