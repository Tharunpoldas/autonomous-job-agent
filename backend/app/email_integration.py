"""Per-user Gmail/Outlook status tracking with periodic background synchronization.

Mailbox access is only available after the user completes the provider's OAuth consent.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .main import db, current_user, event, now_iso, DATA_DIR

router = APIRouter(prefix="/api/email", tags=["email"])
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
STATE_SECRET = os.getenv("EMAIL_OAUTH_STATE_SECRET", "")
TOKEN_KEY = os.getenv("EMAIL_TOKEN_ENCRYPTION_KEY", "")

def _fernet():
    if not TOKEN_KEY:
        raise HTTPException(503, "Email token encryption is not configured")
    try:
        return Fernet(TOKEN_KEY.encode())
    except Exception:
        raise HTTPException(503, "EMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key")

def _state(uid, provider):
    if not STATE_SECRET:
        raise HTTPException(503, "Email OAuth state secret is not configured")
    payload = json.dumps({"uid": uid, "provider": provider, "nonce": secrets.token_urlsafe(24), "ts": int(time.time())}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(STATE_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig

def _read_state(value, provider):
    try:
        body, sig = value.split(".", 1)
        expected = hmac.new(STATE_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError()
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(raw)
        if data["provider"] != provider or int(time.time()) - data["ts"] > 600:
            raise ValueError()
        return data["uid"]
    except Exception:
        raise HTTPException(400, "Invalid or expired email authorization state")

def _tables():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS email_connections(
      user_id TEXT NOT NULL, provider TEXT NOT NULL, email TEXT,
      refresh_token TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY(user_id,provider));
    CREATE TABLE IF NOT EXISTS processed_email_messages(
      user_id TEXT NOT NULL, provider TEXT NOT NULL, message_id TEXT NOT NULL,
      PRIMARY KEY(user_id,provider,message_id));
    """)
    c.commit()
    c.close()

_tables()

def _save(uid, provider, email, refresh):
    token = _fernet().encrypt(refresh.encode()).decode()
    c = db()
    c.execute("""INSERT INTO email_connections(user_id,provider,email,refresh_token,updated_at)
      VALUES(?,?,?,?,?) ON CONFLICT(user_id,provider) DO UPDATE SET
      email=excluded.email,refresh_token=excluded.refresh_token,updated_at=excluded.updated_at""",
      (uid, provider, email, token, now_iso()))
    c.commit(); c.close()

def _connection(uid, provider):
    c=db(); r=c.execute("SELECT * FROM email_connections WHERE user_id=? AND provider=?",(uid,provider)).fetchone(); c.close()
    if not r: return None
    try: refresh=_fernet().decrypt(r["refresh_token"].encode()).decode()
    except (InvalidToken, Exception): return None
    return {"email":r["email"],"refresh_token":refresh}

def _oauth_config(provider):
    if provider == "gmail":
        return (
            os.getenv("GOOGLE_CLIENT_ID"),
            os.getenv("GOOGLE_CLIENT_SECRET"),
            os.getenv(
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000/api/email/gmail/callback"
            )
        )

    return (
        os.getenv("MICROSOFT_CLIENT_ID"),
        os.getenv("MICROSOFT_CLIENT_SECRET"),
        os.getenv(
            "MICROSOFT_REDIRECT_URI",
            "http://localhost:8000/api/email/outlook/callback"
        )
    )

@router.get("/connections")
def connections(user=Depends(current_user)):
    c=db()
    rows=c.execute("SELECT provider,email,updated_at FROM email_connections WHERE user_id=?",(user["id"],)).fetchall()
    c.close()
    return [{"provider":r["provider"],"email":r["email"],"connected":True,"updated_at":r["updated_at"]} for r in rows]

@router.get("/connect/{provider}")
def connect(provider: str, user=Depends(current_user)):
    if provider not in ("gmail","outlook"): raise HTTPException(404,"Unknown email provider")
    client, secret, redirect = _oauth_config(provider)
    if not client or not secret: raise HTTPException(503,f"{provider} OAuth credentials are not configured")
    state=_state(user["id"],provider)
    if provider=="gmail":
        params={"client_id":client,"redirect_uri":redirect,"response_type":"code","scope":"https://www.googleapis.com/auth/gmail.readonly",
                "access_type":"offline","prompt":"consent","state":state}
        url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode(params)
    else:
        tenant=os.getenv("MICROSOFT_TENANT","common")
        params={"client_id":client,"redirect_uri":redirect,"response_type":"code","response_mode":"query",
                "scope":"offline_access User.Read Mail.Read","state":state}
        url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?"+urlencode(params)
    return {"authorization_url":url}

def _token(provider, code):
    client, secret, redirect = _oauth_config(provider)
    if provider=="gmail":
        url="https://oauth2.googleapis.com/token"
        data={"client_id":client,"client_secret":secret,"code":code,"redirect_uri":redirect,"grant_type":"authorization_code"}
    else:
        tenant=os.getenv("MICROSOFT_TENANT","common")
        url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        data={"client_id":client,"client_secret":secret,"code":code,"redirect_uri":redirect,"grant_type":"authorization_code","scope":"offline_access User.Read Mail.Read"}
    r=requests.post(url,data=data,timeout=20); r.raise_for_status(); return r.json()

@router.get("/gmail/callback")
def gmail_callback(code: str = "", state: str = "", error: str = ""):
    if error: return RedirectResponse(FRONTEND_URL+"?email_error=authorization_denied")
    uid=_read_state(state,"gmail")
    try:
        t=_token("gmail",code)
        refresh=t.get("refresh_token")
        if not refresh: raise ValueError("Provider did not return a refresh token; revoke app access and retry consent.")
        access=t["access_token"]
        profile=requests.get("https://www.googleapis.com/gmail/v1/users/me/profile",headers={"Authorization":"Bearer "+access},timeout=20)
        profile.raise_for_status()
        _save(uid,"gmail",profile.json().get("emailAddress",""),refresh)
        event(uid,"EMAIL_CONNECTED","Gmail mailbox authorized",{})
        return RedirectResponse(FRONTEND_URL+"?email_connected=gmail")
    except Exception:
        return RedirectResponse(FRONTEND_URL+"?email_error=gmail")

@router.get("/outlook/callback")
def outlook_callback(code: str = "", state: str = "", error: str = ""):
    if error: return RedirectResponse(FRONTEND_URL+"?email_error=authorization_denied")
    uid=_read_state(state,"outlook")
    try:
        t=_token("outlook",code)
        refresh=t.get("refresh_token")
        if not refresh: raise ValueError("Provider did not return a refresh token")
        access=t["access_token"]
        profile=requests.get("https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
          headers={"Authorization":"Bearer "+access},timeout=20)
        profile.raise_for_status(); p=profile.json()
        _save(uid,"outlook",p.get("mail") or p.get("userPrincipalName",""),refresh)
        event(uid,"EMAIL_CONNECTED","Outlook mailbox authorized",{})
        return RedirectResponse(FRONTEND_URL+"?email_connected=outlook")
    except Exception:
        return RedirectResponse(FRONTEND_URL+"?email_error=outlook")

def _access(provider, refresh):
    client, secret, _ = _oauth_config(provider)
    if provider=="gmail":
        r=requests.post("https://oauth2.googleapis.com/token",data={"client_id":client,"client_secret":secret,"refresh_token":refresh,"grant_type":"refresh_token"},timeout=20)
    else:
        tenant=os.getenv("MICROSOFT_TENANT","common")
        r=requests.post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
          data={"client_id":client,"client_secret":secret,"refresh_token":refresh,"grant_type":"refresh_token","scope":"offline_access User.Read Mail.Read"},timeout=20)
    r.raise_for_status()
    data=r.json()
    if data.get("refresh_token"): _save_refresh(provider, refresh, data["refresh_token"])
    return data["access_token"]

def _save_refresh(provider, old, new):
    c=db()
    rows=c.execute("SELECT user_id FROM email_connections WHERE provider=?",(provider,)).fetchall()
    # Update only rows whose encrypted token decrypts to the token just refreshed.
    f=_fernet()
    for r in rows:
        row=c.execute("SELECT refresh_token FROM email_connections WHERE user_id=? AND provider=?",(r["user_id"],provider)).fetchone()
        try:
            if f.decrypt(row["refresh_token"].encode()).decode()==old:
                c.execute("UPDATE email_connections SET refresh_token=?,updated_at=? WHERE user_id=? AND provider=?",
                  (f.encrypt(new.encode()).decode(),now_iso(),r["user_id"],provider))
        except Exception: pass
    c.commit(); c.close()

def _classify(text):
    t=text.lower()
    rules=[
      ("offered",["offer letter","pleased to offer","job offer","employment offer"]),
      ("rejected",["we regret to inform","not selected","unsuccessful","application was rejected","decided not to move forward"]),
      ("interview",["interview invitation","schedule an interview","invite you to interview","interview availability"]),
      ("shortlisted",["shortlisted","selected for the next round","move to the next round"]),
      ("applied",["application received","thank you for applying","successfully applied","application submitted","received your application"])
    ]
    for status,phrases in rules:
        if any(p in t for p in phrases): return status
    return None

def _already(uid,provider,msgid):
    c=db(); found=c.execute("SELECT 1 FROM processed_email_messages WHERE user_id=? AND provider=? AND message_id=?",(uid,provider,msgid)).fetchone(); c.close()
    return bool(found)

def _mark(uid,provider,msgid):
    c=db(); c.execute("INSERT OR IGNORE INTO processed_email_messages VALUES(?,?,?)",(uid,provider,msgid)); c.commit(); c.close()

def _apply_email(uid,provider,msgid,subject,preview,sender):
    text=(subject+" "+preview+" "+sender).lower()
    status=_classify(text)
    if not status: return False
    apps=[]
    c=db()
    for row in c.execute("SELECT id,data FROM applications WHERE user_id=?",(uid,)).fetchall():
        a=json.loads(row["data"])
        company=(a.get("company") or "").lower()
        title=(a.get("title") or "").lower()
        if (company and company in text) or (title and title in text):
            apps.append((row["id"],a))
    if len(apps)!=1: return False
    appid,a=apps[0]
    order={"pending_approval":0,"prepared":1,"submitted":2,"applied":3,"shortlisted":4,"interview":5,"offered":6,"rejected":6}
    old=a.get("status","")
    if old in ("offered","rejected") or order.get(status,0)<order.get(old,0): return False
    a["status"]=status
    a["status_source"]="email"
    a["status_evidence"]={"provider":provider,"subject":subject[:300],"sender":sender[:200],"message_id":msgid}
    c.execute("UPDATE applications SET data=? WHERE id=? AND user_id=?",(json.dumps(a),appid,uid))
    c.commit(); c.close()
    event(uid,"EMAIL_STATUS",f"Email updated {a.get('title')} at {a.get('company')} to {status}",{"application_id":appid,"provider":provider})
    return True

def _sync_provider(uid,provider):
    conn=_connection(uid,provider)
    if not conn: return {"provider":provider,"updated":0,"error":"not connected or token unavailable"}
    access=_access(provider,conn["refresh_token"])
    if provider=="gmail":
        headers={"Authorization":"Bearer "+access}
        listed=requests.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
          params={"maxResults":50,"q":"newer_than:30d"},headers=headers,timeout=20)
        listed.raise_for_status()
        messages=listed.json().get("messages",[])
        updated=0
        for m in messages:
            mid=m["id"]
            if _already(uid,provider,mid): continue
            r=requests.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
              params={"format":"metadata","metadataHeaders":["Subject","From"]},headers=headers,timeout=20)
            r.raise_for_status(); msg=r.json(); headers_list=msg.get("payload",{}).get("headers",[])
            h={x["name"].lower():x["value"] for x in headers_list}
            updated+=bool(_apply_email(uid,provider,mid,h.get("subject",""),msg.get("snippet",""),h.get("from","")))
            _mark(uid,provider,mid)
    else:
        headers={"Authorization":"Bearer "+access}
        r=requests.get("https://graph.microsoft.com/v1.0/me/messages",
          params={"$top":50,"$select":"id,subject,bodyPreview,from,receivedDateTime","$orderby":"receivedDateTime desc"},
          headers=headers,timeout=20)
        r.raise_for_status(); updated=0
        for m in r.json().get("value",[]):
            mid=m["id"]
            if _already(uid,provider,mid): continue
            sender=(m.get("from") or {}).get("emailAddress",{}).get("address","")
            updated+=bool(_apply_email(uid,provider,mid,m.get("subject",""),m.get("bodyPreview",""),sender))
            _mark(uid,provider,mid)
    return {"provider":provider,"updated":updated}

def sync_user(uid):
    results=[]
    c=db(); providers=[r["provider"] for r in c.execute("SELECT provider FROM email_connections WHERE user_id=?",(uid,)).fetchall()]; c.close()
    for provider in providers:
        try: results.append(_sync_provider(uid,provider))
        except Exception as exc: results.append({"provider":provider,"updated":0,"error":str(exc)[:250]})
    return results

@router.post("/sync")
def sync(user=Depends(current_user)):
    return {"results":sync_user(user["id"])}

@router.delete("/disconnect/{provider}")
def disconnect(provider: str,user=Depends(current_user)):
    if provider not in ("gmail","outlook"): raise HTTPException(404,"Unknown email provider")
    c=db(); c.execute("DELETE FROM email_connections WHERE user_id=? AND provider=?",(user["id"],provider)); c.execute("DELETE FROM processed_email_messages WHERE user_id=? AND provider=?",(user["id"],provider)); c.commit(); c.close()
    event(user["id"],"EMAIL_DISCONNECTED",f"Disconnected {provider}",{})
    return {"ok":True}
