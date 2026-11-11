"""Google Workspace integration tools: Gmail and Google Calendar via official Google APIs."""
import os
import base64
import logging
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("TARS.GoogleWorkspace")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events"
]

# Path configurations
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TOKEN_PATH = CONFIG_DIR / "google_token.json"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"


def get_google_credentials():
    """
    Retrieves or refreshes OAuth2 credentials for Gmail and Google Calendar.
    Returns None if credentials.json is not configured by the user.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            logger.warning(f"Failed to load existing google_token.json: {e}")
            creds = None

    # Refresh expired credentials
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        except Exception as e:
            logger.warning(f"Failed to refresh Google token: {e}")
            creds = None

    if not creds:
        # Check for client secrets file
        target_cred_path = CREDENTIALS_PATH if CREDENTIALS_PATH.exists() else (CONFIG_DIR.parent / "credentials.json")
        if target_cred_path.exists():
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(str(target_cred_path), SCOPES)
                creds = flow.run_local_server(port=0)
                with open(TOKEN_PATH, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())
            except Exception as e:
                logger.error(f"OAuth flow error: {e}")
                return None
        else:
            return None

    return creds


# --- Gmail Tools ---

def list_unread_emails(max_results: int = 5) -> Dict[str, Any]:
    """
    Retrieves the most recent unread emails from Gmail with sender, subject, and snippet.
    """
    creds = get_google_credentials()
    if not creds:
        return {
            "success": False,
            "error": "Google Workspace not authenticated. Place your Google OAuth 'credentials.json' in config/credentials.json to enable Gmail.",
            "emails": []
        }

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        results = service.users().messages().list(userId="me", q="is:unread", maxResults=max_results).execute()
        messages = results.get("messages", [])

        email_list = []
        for msg_meta in messages:
            msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="metadata").execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(No Subject)")
            sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "(Unknown Sender)")
            date = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
            snippet = msg.get("snippet", "")
            email_list.append({
                "id": msg_meta["id"],
                "from": sender,
                "subject": subject,
                "date": date,
                "snippet": snippet
            })

        return {
            "success": True,
            "count": len(email_list),
            "emails": email_list
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to list Gmail messages: {str(e)}", "emails": []}


def read_email(message_id: str) -> Dict[str, Any]:
    """
    Retrieves the full body text and headers of a specific Gmail message by ID.
    """
    creds = get_google_credentials()
    if not creds:
        return {
            "success": False,
            "error": "Google Workspace not authenticated. Place 'credentials.json' in config/."
        }

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = msg.get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(No Subject)")
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "(Unknown Sender)")

        # Extract plain text body
        body = ""
        payload = msg.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                        break
        elif "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        return {
            "success": True,
            "id": message_id,
            "from": sender,
            "subject": subject,
            "body": body or msg.get("snippet", "")
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to read email {message_id}: {str(e)}"}


def send_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    """
    Sends an email via Gmail API.
    NOTE: This is a destructive/sensitive tool and strictly requires human confirmation.
    """
    creds = get_google_credentials()
    if not creds:
        return {
            "success": False,
            "error": "Google Workspace not authenticated. Place 'credentials.json' in config/."
        }

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        sent_msg = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {
            "success": True,
            "message_id": sent_msg.get("id"),
            "to": to,
            "subject": subject,
            "message": f"Email successfully sent to {to}."
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to send email: {str(e)}"}


# --- Google Calendar Tools ---

def list_upcoming_events(max_results: int = 5) -> Dict[str, Any]:
    """
    Lists upcoming Google Calendar events from the primary calendar.
    """
    creds = get_google_credentials()
    if not creds:
        return {
            "success": False,
            "error": "Google Workspace not authenticated. Place 'credentials.json' in config/.",
            "events": []
        }

    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)
        now_iso = datetime.now(timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId="primary",
            timeMin=now_iso,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        items = events_result.get("items", [])
        event_list = []
        for item in items:
            start = item.get("start", {}).get("dateTime", item.get("start", {}).get("date"))
            event_list.append({
                "id": item.get("id"),
                "summary": item.get("summary", "(No Title)"),
                "start": start,
                "location": item.get("location", "")
            })

        return {
            "success": True,
            "count": len(event_list),
            "events": event_list
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to list calendar events: {str(e)}", "events": []}


def create_calendar_event(summary: str, start_time: str, end_time: Optional[str] = None, description: Optional[str] = None) -> Dict[str, Any]:
    """
    Creates an event on the user's primary Google Calendar.

    Args:
        summary: Event title / summary (e.g. 'Project Sync', 'Dentist Appointment').
        start_time: ISO 8601 string or date string (e.g. '2026-11-12T10:00:00Z').
        end_time: Optional ISO 8601 string. Defaults to 1 hour after start_time.
        description: Optional notes or description for the event.
    """
    creds = get_google_credentials()
    if not creds:
        return {
            "success": False,
            "error": "Google Workspace not authenticated. Place 'credentials.json' in config/."
        }

    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)

        event_body = {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time or start_time}
        }
        created = service.events().insert(calendarId="primary", body=event_body).execute()
        return {
            "success": True,
            "event_id": created.get("id"),
            "htmlLink": created.get("htmlLink"),
            "summary": summary,
            "message": f"Event '{summary}' scheduled on Google Calendar."
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to create calendar event: {str(e)}"}

