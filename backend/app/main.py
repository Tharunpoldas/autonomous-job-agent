
import os
import re
import json
import html
import time as _time
import hmac
import hashlib
import secrets
import sqlite3
import threading
import urllib.parse

from datetime import datetime, timedelta
from typing import List, Optional, Dict

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH, override=True)

from fastapi import FastAPI
# Load backend/.env before importing email integration.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.getenv(
    "CAREER_AGENT_DB",
    os.path.join(DATA_DIR, "career_agent.db")
)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))

app = FastAPI(
    title="Autonomous Job Automation Agent",
    version="3.2.0",
    description=(
        "Multi-user AI job search, resume customization, "
        "application tracking and recruiter outreach."
    )
)
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "database": os.path.exists(DB_PATH),
        "jobs": len(all_jobs()),
        "ai_configured": bool(os.getenv("GEMINI_API_KEY")),
        "email_configured": bool(os.getenv("RESEND_API_KEY")),
        "email_sender": os.getenv("EMAIL_FROM", "not-configured"),
        "gmail_oauth_configured": bool(
            os.getenv("GOOGLE_CLIENT_ID")
            and os.getenv("GOOGLE_CLIENT_SECRET")
        ),
        "outlook_oauth_configured": bool(
            os.getenv("MICROSOFT_CLIENT_ID")
            and os.getenv("MICROSOFT_CLIENT_SECRET")
        ),
    }
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_PROFILE = {
    "name": "Candidate",
    "email": "",
    "titles": ["AI Engineer", "Machine Learning Engineer"],
    "locations": ["Bengaluru", "Hyderabad", "Remote"],
    "experience": "Entry Level",
    "salary": "",
    "work_modes": ["Remote", "Hybrid", "On-site"],
    "skills": ["Python", "SQL", "Machine Learning"],
    "projects": [],
    "education": "",
}

DEMO_JOBS = [
    {
        "id": "demo-001",
        "title": "AI Engineer",
        "company": "Nova AI Labs",
        "location": "Bengaluru",
        "mode": "Hybrid",
        "salary": "8-14 LPA",
        "source": "Demo Portal",
        "url": "https://example.com/jobs/demo-001",
        "description": (
            "Build Python machine learning systems using PyTorch, "
            "computer vision, NLP, APIs and FastAPI."
        ),
    },
    {
        "id": "demo-002",
        "title": "Computer Vision Engineer",
        "company": "VisionWorks",
        "location": "Hyderabad",
        "mode": "On-site",
        "salary": "7-12 LPA",
        "source": "Demo Portal",
        "url": "https://example.com/jobs/demo-002",
        "description": (
            "Develop image processing and computer vision models "
            "using Python, OpenCV, TensorFlow and PyTorch."
        ),
    },
    {
        "id": "demo-003",
        "title": "Full Stack Developer",
        "company": "CloudNova",
        "location": "Remote",
        "mode": "Remote",
        "salary": "6-10 LPA",
        "source": "Demo Portal",
        "url": "https://example.com/jobs/demo-003",
        "description": (
            "Build React and FastAPI applications, REST APIs, "
            "SQL databases and production web services."
        ),
    },
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


def job_hash(job):
    text = "|".join([
        str(job.get("company", "")),
        str(job.get("title", "")),
        str(job.get("location", "")),
        str(job.get("description", "")),
    ])
    return hashlib.sha256(text.lower().encode()).hexdigest()


def hash_password(password: str, salt: Optional[bytes] = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, 180_000
    )
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str):
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, 180_000
        ).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except Exception:
        return False


def token_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def init_db():
    connection = db()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profiles (
            user_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            job_hash TEXT UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outreach (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            time TEXT NOT NULL,
            kind TEXT,
            message TEXT,
            meta TEXT
        );
    """)

    for table in ("applications", "outreach"):
        cols = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if "user_id" not in cols:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id TEXT"
            )

    if not connection.execute(
        "SELECT 1 FROM jobs LIMIT 1"
    ).fetchone():
        for job in DEMO_JOBS:
            upsert_job(connection, job)

    connection.commit()
    connection.close()


def upsert_job(connection, job):
    connection.execute(
        """
        INSERT OR IGNORE INTO jobs(id,data,job_hash,created_at)
        VALUES(?,?,?,?)
        """,
        (
            job["id"],
            json.dumps(job),
            job_hash(job),
            now_iso(),
        ),
    )


def create_session(user_id: str):
    raw = secrets.token_urlsafe(48)
    connection = db()
    connection.execute(
        """
        INSERT INTO sessions(token_hash,user_id,expires_at)
        VALUES(?,?,?)
        """,
        (
            token_hash(raw),
            user_id,
            (
                datetime.utcnow() + timedelta(days=SESSION_DAYS)
            ).isoformat() + "Z",
        ),
    )
    connection.commit()
    connection.close()
    return raw


def current_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Please sign in first"
        )

    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Invalid session token"
        )

    connection = db()
    row = connection.execute(
        """
        SELECT u.id, u.email, u.created_at, s.expires_at
        FROM sessions s
        JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=?
        """,
        (token_hash(raw),),
    ).fetchone()

    if not row:
        connection.close()
        raise HTTPException(
            status_code=401,
            detail="Invalid session"
        )

    if row["expires_at"] < datetime.utcnow().isoformat() + "Z":
        connection.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (token_hash(raw),),
        )
        connection.commit()
        connection.close()
        raise HTTPException(
            status_code=401,
            detail="Session expired. Please sign in again"
        )

    connection.close()
    return {"id": row["id"], "email": row["email"]}


def profile_data(user_id):
    connection = db()
    row = connection.execute(
        "SELECT data FROM profiles WHERE user_id=?",
        (user_id,),
    ).fetchone()
    connection.close()
    return json.loads(row["data"]) if row else dict(DEFAULT_PROFILE)


def save_profile(user_id, profile):
    connection = db()
    connection.execute(
        """
        INSERT INTO profiles(user_id,data,updated_at)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
        data=excluded.data,
        updated_at=excluded.updated_at
        """,
        (user_id, json.dumps(profile), now_iso()),
    )
    connection.commit()
    connection.close()


def all_jobs():
    connection = db()
    rows = connection.execute(
        "SELECT data FROM jobs"
    ).fetchall()
    connection.close()
    return [json.loads(row["data"]) for row in rows]


def all_records(table, user_id):
    if table not in {"applications", "outreach"}:
        raise ValueError("Invalid table")

    connection = db()
    rows = connection.execute(
        f"""
        SELECT data FROM {table}
        WHERE user_id=?
        ORDER BY rowid DESC
        """,
        (user_id,),
    ).fetchall()
    connection.close()
    return [json.loads(row["data"]) for row in rows]


def event(user_id, kind, message, meta=None):
    connection = db()
    connection.execute(
        """
        INSERT INTO events(user_id,time,kind,message,meta)
        VALUES(?,?,?,?,?)
        """,
        (
            user_id,
            now_iso(),
            kind,
            message,
            json.dumps(meta or {}),
        ),
    )
    connection.commit()
    connection.close()


def events(user_id):
    connection = db()
    rows = connection.execute(
        """
        SELECT time,kind,message,meta
        FROM events
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 100
        """,
        (user_id,),
    ).fetchall()
    connection.close()

    result = []
    for row in rows:
        try:
            metadata = json.loads(row["meta"])
        except Exception:
            metadata = {}

        result.append({
            "time": row["time"],
            "kind": row["kind"],
            "message": row["message"],
            "meta": metadata,
        })
    return result


def tokens(value):
    if not value:
        return set()
    return set(
        re.findall(
            r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}",
            str(value).lower()
        )
    )


def match(job, user_id):
    profile = profile_data(user_id)
    required = tokens(job.get("description", ""))
    profile_skills = {
        str(s).lower() for s in profile.get("skills", [])
    }
    title_words = tokens(job.get("title", ""))
    target_words = set()

    for title in profile.get("titles", []):
        target_words.update(tokens(title))

    skill_hits = required & profile_skills
    skill_score = min(
        100,
        len(skill_hits) / max(1, min(len(profile_skills), 10)) * 100
    )
    title_score = 100 if title_words & target_words else 35

    locations = profile.get("locations", [])
    location = str(job.get("location", ""))
    loc_score = (
        100
        if location in locations
        or str(job.get("mode", "")).lower() == "remote"
        else 35
    )

    score = round(
        0.55 * skill_score
        + 0.30 * title_score
        + 0.15 * loc_score
    )

    return {
        "score": score,
        "matched_skills": sorted(skill_hits),
        "missing_skills": sorted(required - profile_skills)[:12],
    }


def normalize_job(job, source):
    title = job.get("title") or job.get("name") or "Untitled Role"
    company = (
        job.get("company_name")
        or job.get("company")
        or "Unknown Company"
    )
    location = (
        job.get("candidate_required_location")
        or job.get("location")
        or "Remote"
    )

    if isinstance(location, list):
        location = ", ".join(str(x) for x in location)

    location_text = str(location)
    job_type = str(job.get("job_type", ""))
    mode = (
        "Remote"
        if "remote" in location_text.lower()
        or "remote" in job_type.lower()
        else "On-site"
    )

    description = (
        job.get("description")
        or job.get("description_text")
        or ""
    )
    url = (
        job.get("url")
        or job.get("redirect_url")
        or job.get("apply_url")
        or ""
    )
    identifier = url or company + title + location_text
    job_id = (
        f"{source.lower()}-"
        f"{hashlib.sha1(identifier.encode()).hexdigest()[:14]}"
    )

    def clean(value):
        return re.sub(
            r"<[^>]+>", " ", html.unescape(str(value))
        ).strip()

    clean_description = re.sub(
        r"\s+", " ", clean(description)
    ).strip()

    return {
        "id": job_id,
        "title": clean(title),
        "company": clean(company),
        "location": clean(location_text),
        "mode": mode,
        "salary": (
            job.get("salary")
            or job.get("salary_range")
            or "Not listed"
        ),
        "source": source,
        "url": url,
        "description": clean_description[:12000],
    }


def fetch_remotive():
    response = requests.get(
        "https://remotive.com/api/remote-jobs",
        timeout=20,
        headers={"User-Agent": "CareerAgent/3.0"},
    )
    response.raise_for_status()
    return [
        normalize_job(j, "Remotive")
        for j in response.json().get("jobs", [])
    ]


def fetch_arbeitnow():
    response = requests.get(
        "https://www.arbeitnow.com/api/job-board-api",
        timeout=20,
        headers={"User-Agent": "CareerAgent/3.0"},
    )
    response.raise_for_status()
    return [
        normalize_job(j, "Arbeitnow")
        for j in response.json().get("data", [])
    ]


def fetch_adzuna(query="software engineer"):
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        return [], "ADZUNA_APP_ID/ADZUNA_APP_KEY not configured"

    url = (
        "https://api.adzuna.com/v1/api/jobs/in/search/1"
        f"?app_id={urllib.parse.quote(app_id)}"
        f"&app_key={urllib.parse.quote(app_key)}"
        "&results_per_page=30"
        f"&what={urllib.parse.quote(query)}"
        "&content-type=application/json"
    )

    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "CareerAgent/3.0"},
    )
    response.raise_for_status()

    return [
        normalize_job(j, "Adzuna")
        for j in response.json().get("results", [])
    ], None


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    name: str = "Candidate"
    email: str = ""
    titles: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    experience: str = "Entry Level"
    salary: str = ""
    work_modes: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    education: str = ""


class ApplicationRequest(BaseModel):
    job_id: str
    mode: str = "approval"
    resume_version: Optional[str] = None
    answers: Dict[str, str] = Field(default_factory=dict)


class OutreachRequest(BaseModel):
    job_id: str
    recruiter_name: str = "Hiring Team"
    recruiter_email: str
    subject: Optional[str] = None
    body: Optional[str] = None


init_db()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "database": os.path.exists(DB_PATH),
        "jobs": len(all_jobs()),
        "ai_configured": bool(os.getenv("GEMINI_API_KEY")),
        "email_configured": bool(os.getenv("RESEND_API_KEY")),
        "email_sender": os.getenv("EMAIL_FROM", "not-configured"),
        "gmail_oauth_configured": bool(
            os.getenv("GOOGLE_CLIENT_ID")
            and os.getenv("GOOGLE_CLIENT_SECRET")
        ),
        "outlook_oauth_configured": bool(
            os.getenv("MICROSOFT_CLIENT_ID")
            and os.getenv("MICROSOFT_CLIENT_SECRET")
        ),
    }


@app.post("/api/auth/register")
def register(req: RegisterRequest):
    email = req.email.strip().lower()

    if not EMAIL_RE.match(email):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid email address"
        )

    if (
        not re.search(r"[A-Za-z]", req.password)
        or not re.search(r"\d", req.password)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Password must contain at least one letter "
                "and one number"
            )
        )

    user_id = "usr-" + secrets.token_hex(12)
    profile = dict(DEFAULT_PROFILE)
    profile.update({
        "name": req.name.strip(),
        "email": email,
    })

    connection = db()
    try:
        connection.execute(
            """
            INSERT INTO users(id,email,password_hash,created_at,last_login)
            VALUES(?,?,?,?,?)
            """,
            (
                user_id,
                email,
                hash_password(req.password),
                now_iso(),
                now_iso(),
            ),
        )
        connection.execute(
            """
            INSERT INTO profiles(user_id,data,updated_at)
            VALUES(?,?,?)
            """,
            (user_id, json.dumps(profile), now_iso()),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.close()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists"
        )

    connection.close()
    token = create_session(user_id)
    event(user_id, "ACCOUNT_CREATED", "Account created")

    return {
        "token": token,
        "user": {
            "id": user_id,
            "name": profile["name"],
            "email": email,
        },
    }


@app.post("/api/auth/login")
def login(req: LoginRequest):
    email = req.email.strip().lower()
    connection = db()

    row = connection.execute(
        "SELECT id,password_hash FROM users WHERE email=?",
        (email,),
    ).fetchone()

    if not row or not verify_password(
        req.password, row["password_hash"]
    ):
        connection.close()
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password"
        )

    connection.execute(
        "UPDATE users SET last_login=? WHERE id=?",
        (now_iso(), row["id"]),
    )
    connection.commit()
    connection.close()

    token = create_session(row["id"])
    profile = profile_data(row["id"])
    event(row["id"], "LOGIN", "Signed in")

    return {
        "token": token,
        "user": {
            "id": row["id"],
            "name": profile.get("name", "Candidate"),
            "email": email,
        },
    }


@app.post("/api/auth/logout")
def logout(
    authorization: Optional[str] = Header(default=None),
    user=Depends(current_user),
):
    raw = authorization.split(" ", 1)[1].strip()
    connection = db()
    connection.execute(
        "DELETE FROM sessions WHERE token_hash=?",
        (token_hash(raw),),
    )
    connection.commit()
    connection.close()
    return {"success": True}


@app.get("/api/account/me")
def account_me(user=Depends(current_user)):
    profile = profile_data(user["id"])
    return {"user": user, "profile": profile}


@app.get("/api/profile")
def get_profile(user=Depends(current_user)):
    return profile_data(user["id"])


@app.put("/api/profile")
def update_profile(req: ProfileUpdate, user=Depends(current_user)):
    data = (
        req.model_dump()
        if hasattr(req, "model_dump")
        else req.dict()
    )
    data["email"] = user["email"]
    save_profile(user["id"], data)
    event(user["id"], "PROFILE_UPDATED", "Candidate profile updated")
    return profile_data(user["id"])


@app.get("/api/integrations")
def integrations(user=Depends(current_user)):
    adzuna_configured = bool(
        os.getenv("ADZUNA_APP_ID")
        and os.getenv("ADZUNA_APP_KEY")
    )
    gemini_configured = bool(os.getenv("GEMINI_API_KEY"))
    resend_configured = bool(os.getenv("RESEND_API_KEY"))

    return {
        "sources": [
            {
                "name": "Remotive",
                "enabled": True,
                "configured": True,
            },
            {
                "name": "Arbeitnow",
                "enabled": True,
                "configured": True,
            },
            {
                "name": "Adzuna",
                "enabled": adzuna_configured,
                "configured": adzuna_configured,
            },
            {
                "name": "Resend",
                "enabled": resend_configured,
                "configured": resend_configured,
                "sender": os.getenv("EMAIL_FROM", "not-configured"),
            },
            {
                "name": "LinkedIn/Naukri/Indeed",
                "enabled": False,
                "configured": False,
                "note": (
                    "Use official/authorized integrations; "
                    "no anti-bot bypassing."
                ),
            },
        ],
        "ai": {
            "gemini_configured": gemini_configured,
            "model": os.getenv(
                "GEMINI_MODEL", "gemini-3.8-flash"
            ),
        },
        "email_accounts": {
            "gmail_oauth_configured": bool(
                os.getenv("GOOGLE_CLIENT_ID")
                and os.getenv("GOOGLE_CLIENT_SECRET")
            ),
            "outlook_oauth_configured": bool(
                os.getenv("MICROSOFT_CLIENT_ID")
                and os.getenv("MICROSOFT_CLIENT_SECRET")
            ),
        },
    }


@app.get("/api/jobs")
def jobs(user=Depends(current_user)):
    result = [
        {
            **job,
            "match": match(job, user["id"]),
            "hash": job_hash(job),
        }
        for job in all_jobs()
    ]
    return sorted(
        result,
        key=lambda x: x["match"]["score"],
        reverse=True
    )


@app.post("/api/jobs/sync")
def sync_jobs(user=Depends(current_user)):
    results, errors = [], []

    for name, function in [
        ("Remotive", fetch_remotive),
        ("Arbeitnow", fetch_arbeitnow),
    ]:
        try:
            results.extend(function())
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    try:
        profile = profile_data(user["id"])
        query = (
            profile.get("titles")
            or ["software engineer"]
        )[0]
        adzuna_jobs, error = fetch_adzuna(query)
        results.extend(adzuna_jobs)
        if error:
            errors.append("Adzuna: " + error)
    except Exception as exc:
        errors.append("Adzuna: " + str(exc))

    connection = db()
    before = connection.execute(
        "SELECT COUNT(*) AS n FROM jobs"
    ).fetchone()["n"]

    for job in results:
        upsert_job(connection, job)

    connection.commit()
    after = connection.execute(
        "SELECT COUNT(*) AS n FROM jobs"
    ).fetchone()["n"]
    connection.close()

    event(
        user["id"],
        "JOB_SYNC",
        f"Synced {len(results)} live jobs; database now has {after} jobs",
        {"errors": errors},
    )

    return {
        "fetched": len(results),
        "database_jobs": after,
        "new_jobs": max(0, after - before),
        "errors": errors,
    }


@app.get("/api/dashboard")
def dashboard(user=Depends(current_user)):
    jobs_list = all_jobs()
    applications_list = all_records(
        "applications", user["id"]
    )
    outreach_list = all_records("outreach", user["id"])

    statuses = {}
    for application in applications_list:
        status = application.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1

    return {
        "jobs_discovered": len(jobs_list),
        "relevant_jobs": sum(
            match(j, user["id"])["score"] >= 60
            for j in jobs_list
        ),
        "applications": len(applications_list),
        "applications_by_status": statuses,
        "emails_sent": sum(
            x.get("status") == "sent"
            for x in outreach_list
        ),
        "emails_failed": sum(
            x.get("status") == "failed"
            for x in outreach_list
        ),
        "draft_emails": sum(
            x.get("status") == "draft"
            for x in outreach_list
        ),
        "replies": sum(
            x.get("reply") is not None
            for x in outreach_list
        ),
        "events": events(user["id"])[:30],
    }


@app.get("/api/events")
def get_events(user=Depends(current_user)):
    return events(user["id"])


def latex_escape(value):
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(
        replacements.get(ch, ch)
        for ch in str(value or "")
    )


def build_resume_latex(resume_data):
    def section(title, content, bullet=False):
        if not content:
            return ""

        output = [
            r"\section*{" + latex_escape(title) + "}"
        ]

        if bullet:
            output.append(r"\begin{itemize}")
            output.extend(
                r"\item " + latex_escape(item)
                for item in content
            )
            output.append(r"\end{itemize}")
        else:
            output.append(latex_escape(content))

        return "\n".join(output) + "\n"

    parts = [
        r"\documentclass[10pt,a4paper]{article}",
        r"\usepackage[margin=0.7in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{5pt}",
        r"\begin{document}",
        r"\begin{center}",
        r"{\LARGE\textbf{"
        + latex_escape(resume_data.get("name", "Candidate"))
        + "}}",
        r"\\",
        latex_escape(resume_data.get("email", "")),
        r"\end{center}",
        section(
            "Professional Summary",
            resume_data.get("summary", "")
        ),
        section(
            "Technical Skills",
            resume_data.get("skills", []),
            True
        ),
        section(
            "Projects",
            resume_data.get("projects", []),
            True
        ),
        section(
            "Education",
            resume_data.get("education", "")
        ),
    ]

    experience = resume_data.get("experience", "")
    if experience and experience.strip().lower() not in {
        "entry level", "fresher", "none"
    }:
        parts.append(section("Experience", experience))

    parts.append(r"\end{document}")
    return "\n".join(parts)


def local_resume(job, user_id):
    profile = profile_data(user_id)
    matching = match(job, user_id)
    matched = matching.get("matched_skills", [])
    profile_skills = profile.get("skills", [])

    skills = []
    seen = set()
    for skill in matched + profile_skills:
        skill = str(skill).strip()
        if skill and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())

    projects = [
        str(p).strip()
        for p in profile.get("projects", [])
        if str(p).strip()
    ]
    education = str(profile.get("education", "")).strip()
    experience = str(profile.get("experience", "")).strip()
    target = job.get("title", "the selected role")
    company = job.get("company", "the hiring organization")
    project_text = ", ".join(projects[:3])
    skills_text = ", ".join(skills[:6])

    summary = (
        f"{experience or 'Entry-level'} candidate targeting "
        f"{target} at {company}. "
        f"Technical skills include "
        f"{skills_text or 'the skills listed below'}."
    )
    if project_text:
        summary += f" Relevant projects include {project_text}."

    return {
        "name": profile.get("name", "Candidate"),
        "email": profile.get("email", ""),
        "summary": summary,
        "skills": skills,
        "projects": projects,
        "education": education,
        "experience": experience,
    }


def ai_resume(job, user_id):
    resume_data = local_resume(job, user_id)
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return {
            **resume_data,
            "method": "Local ATS optimizer",
        }

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = f"""
Write one professional, concise resume summary tailored to
the selected job. Use only verified candidate facts.
Do not invent qualifications, work experience, achievements,
metrics, or skills. Return only the summary paragraph.

Candidate:
{json.dumps(resume_data, ensure_ascii=False)}

Job:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Description: {job.get('description', '')[:5000]}
"""
        response = client.models.generate_content(
            model=os.getenv(
                "GEMINI_MODEL", "gemini-3.8-flash"
            ),
            contents=prompt,
        )
        summary = (response.text or "").strip()
        if summary:
            resume_data["summary"] = summary

        return {
            **resume_data,
            "method": "Gemini + ATS optimizer",
        }
    except Exception as exc:
        return {
            **resume_data,
            "method": "Local ATS optimizer",
            "ai_fallback_error": str(exc),
        }


@app.post("/api/resume/{job_id}")
def resume(job_id: str, user=Depends(current_user)):
    job = next(
        (j for j in all_jobs() if j["id"] == job_id),
        None
    )
    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    matching = match(job, user["id"])
    resume_data = ai_resume(job, user["id"])
    version = (
        f"resume-{job_id}-"
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    )
    latex = build_resume_latex(resume_data)

    event(
        user["id"],
        "RESUME_CUSTOMIZED",
        f"Generated {version}",
        {
            "job_id": job_id,
            "score": matching["score"],
            "method": resume_data.get("method"),
        },
    )

    return {
        "version": version,
        "job_id": job_id,
        "match": matching,
        "resume": resume_data,
        "latex": latex,
        "ai": {
            "method": resume_data.get("method"),
            "text": resume_data.get("summary", ""),
        },
    }


@app.post("/api/applications")
def apply(req: ApplicationRequest, user=Depends(current_user)):
    job = next(
        (j for j in all_jobs() if j["id"] == req.job_id),
        None
    )
    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    jhash = job_hash(job)
    existing = next(
        (
            a for a in all_records("applications", user["id"])
            if a.get("job_hash") == jhash
        ),
        None,
    )
    if existing:
        return {"duplicate": True, "application": existing}

    if req.mode not in {"approval", "assisted"}:
        raise HTTPException(
            status_code=400,
            detail="Application mode must be approval or assisted"
        )

    status = (
        "pending_approval"
        if req.mode == "approval"
        else "prepared"
    )

    application = {
        "id": (
            "app-"
            + datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:-3]
        ),
        "user_id": user["id"],
        "job_id": job["id"],
        "job_hash": jhash,
        "company": job["company"],
        "title": job["title"],
        "portal": job["source"],
        "resume_version": req.resume_version or "ai-generated",
        "created_at": now_iso(),
        "status": status,
        "answers": req.answers,
        "job_url": job.get("url", ""),
    }

    connection = db()
    connection.execute(
        "INSERT INTO applications(id,user_id,data) VALUES(?,?,?)",
        (
            application["id"],
            user["id"],
            json.dumps(application),
        ),
    )
    connection.commit()
    connection.close()

    event(
        user["id"],
        "APPLICATION",
        f"{status}: {job['title']} at {job['company']}",
        application,
    )
    return {"duplicate": False, "application": application}


@app.post("/api/applications/{app_id}/approve")
def approve(app_id: str, user=Depends(current_user)):
    connection = db()
    row = connection.execute(
        """
        SELECT data FROM applications
        WHERE id=? AND user_id=?
        """,
        (app_id, user["id"]),
    ).fetchone()

    if not row:
        connection.close()
        raise HTTPException(
            status_code=404,
            detail="Application not found"
        )

    application = json.loads(row["data"])
    if application.get("status") != "pending_approval":
        connection.close()
        raise HTTPException(
            status_code=409,
            detail="Application is not awaiting approval"
        )

    # This records approval only. It does not submit the
    # application to an external job portal.
    application["status"] = "approved"
    application["approved_at"] = now_iso()

    connection.execute(
        """
        UPDATE applications SET data=?
        WHERE id=? AND user_id=?
        """,
        (json.dumps(application), app_id, user["id"]),
    )
    connection.commit()
    connection.close()

    event(
        user["id"],
        "APPLICATION_APPROVED",
        f"Approved {app_id}; external submission not performed",
        application,
    )
    return application


@app.get("/api/applications")
def applications(user=Depends(current_user)):
    return all_records("applications", user["id"])


@app.post("/api/outreach")
def outreach(req: OutreachRequest, user=Depends(current_user)):
    job = next(
        (j for j in all_jobs() if j["id"] == req.job_id),
        None
    )
    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    recipient = req.recruiter_email.strip()
    if not EMAIL_RE.match(recipient):
        raise HTTPException(
            status_code=400,
            detail="Invalid recruiter email address"
        )

    profile = profile_data(user["id"])
    body = req.body or (
        f"Hi {req.recruiter_name},\n\n"
        f"I’m reaching out regarding the {job['title']} "
        f"opportunity at {job['company']}.\n\n"
        f"My background includes "
        f"{', '.join(profile.get('skills', [])[:5])} "
        f"and projects in "
        f"{', '.join(profile.get('projects', [])[:2])}.\n\n"
        "I’d be glad to share my tailored resume and discuss "
        "whether my profile fits the team.\n\n"
        f"Best regards,\n{profile['name']}"
    )

    item = {
        "id": (
            "email-"
            + datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:-3]
        ),
        "user_id": user["id"],
        "job_id": req.job_id,
        "to": recipient,
        "subject": (
            req.subject
            or f"{job['title']} — {profile['name']}"
        ),
        "body": body,
        "status": "draft",
        "reply": None,
    }

    connection = db()
    connection.execute(
        "INSERT INTO outreach(id,user_id,data) VALUES(?,?,?)",
        (item["id"], user["id"], json.dumps(item)),
    )
    connection.commit()
    connection.close()

    event(
        user["id"],
        "OUTREACH_DRAFT",
        f"Prepared recruiter email for {job['company']}",
        item,
    )
    return item


@app.put("/api/outreach/{email_id}")
def update_outreach(
    email_id: str,
    req: OutreachRequest,
    user=Depends(current_user),
):
    connection = db()
    row = connection.execute(
        """
        SELECT data FROM outreach
        WHERE id=? AND user_id=?
        """,
        (email_id, user["id"]),
    ).fetchone()

    if not row:
        connection.close()
        raise HTTPException(
            status_code=404,
            detail="Email not found"
        )

    item = json.loads(row["data"])
    if item.get("status") == "sent":
        connection.close()
        raise HTTPException(
            status_code=409,
            detail="Sent emails cannot be edited"
        )

    recipient = req.recruiter_email.strip()
    if not EMAIL_RE.match(recipient):
        connection.close()
        raise HTTPException(
            status_code=400,
            detail="Invalid recruiter email address"
        )

    item["to"] = recipient
    item["subject"] = req.subject or item.get("subject", "")
    item["body"] = req.body or item.get("body", "")

    connection.execute(
        """
        UPDATE outreach SET data=?
        WHERE id=? AND user_id=?
        """,
        (json.dumps(item), email_id, user["id"]),
    )
    connection.commit()
    connection.close()

    event(
        user["id"],
        "OUTREACH_UPDATED",
        f"Updated recruiter email {email_id}",
        item,
    )
    return item


@app.post("/api/outreach/{email_id}/send")
def send_outreach(email_id: str, user=Depends(current_user)):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv(
        "EMAIL_FROM", "onboarding@resend.dev"
    )

    if not resend_api_key:
        raise HTTPException(
            status_code=500,
            detail="RESEND_API_KEY is not configured in .env"
        )

    connection = db()
    row = connection.execute(
        """
        SELECT data FROM outreach
        WHERE id=? AND user_id=?
        """,
        (email_id, user["id"]),
    ).fetchone()
    connection.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Email not found"
        )

    item = json.loads(row["data"])
    if item.get("status") == "sent":
        return {
            "success": True,
            "message": "Email was already sent",
            "email": item,
        }

    recipient = str(item.get("to", "")).strip()
    if not EMAIL_RE.match(recipient):
        raise HTTPException(
            status_code=400,
            detail="Invalid recipient email address"
        )

    html_body = (
        "<html><body>"
        + html.escape(item.get("body", "")).replace("\n", "<br>")
        + "</body></html>"
    )

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": email_from,
                "to": [recipient],
                "subject": item["subject"],
                "html": html_body,
                "reply_to": user["email"],
            },
            timeout=20,
        )
        response.raise_for_status()
        resend_data = response.json()

        item.update({
            "status": "sent",
            "sent_at": now_iso(),
            "provider": "Resend",
            "provider_id": resend_data.get("id"),
            "from": email_from,
        })

        connection = db()
        connection.execute(
            """
            UPDATE outreach SET data=?
            WHERE id=? AND user_id=?
            """,
            (json.dumps(item), email_id, user["id"]),
        )
        connection.commit()
        connection.close()

        event(
            user["id"],
            "EMAIL_SENT",
            f"Email {email_id} successfully sent through Resend",
            item,
        )

        return {
            "success": True,
            "message": "Email sent successfully",
            "email": item,
            "resend": resend_data,
        }

    except requests.exceptions.RequestException as exc:
        item["status"] = "failed"
        connection = db()
        connection.execute(
            """
            UPDATE outreach SET data=?
            WHERE id=? AND user_id=?
            """,
            (json.dumps(item), email_id, user["id"]),
        )
        connection.commit()
        connection.close()

        error_detail = str(exc)
        if exc.response is not None:
            try:
                error_detail = exc.response.json()
            except Exception:
                error_detail = exc.response.text

        event(
            user["id"],
            "EMAIL_FAILED",
            f"Failed to send email {email_id}",
            {"error": str(error_detail), "email": item},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Resend failed to send the email",
                "error": error_detail,
            },
        )

    except Exception as exc:
        item["status"] = "failed"
        connection = db()
        connection.execute(
            """
            UPDATE outreach SET data=?
            WHERE id=? AND user_id=?
            """,
            (json.dumps(item), email_id, user["id"]),
        )
        connection.commit()
        connection.close()

        event(
            user["id"],
            "EMAIL_FAILED",
            f"Unexpected error sending {email_id}",
            {"error": str(exc)},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected email sending error: {exc}",
        )


# Gmail and Outlook integration.
# Import after the functions above because email_integration
# may import functions from this module.
from .email_integration import router as email_router
from .email_integration import sync_user as sync_email_user

app.include_router(email_router)


# Periodic mailbox synchronization.
_email_worker_started = False


@app.on_event("startup")
def start_email_sync_worker():
    global _email_worker_started

    if _email_worker_started:
        return

    _email_worker_started = True

    def worker():
        while True:
            try:
                connection = db()
                try:
                    user_ids = [
                        r["user_id"]
                        for r in connection.execute(
                            """
                            SELECT DISTINCT user_id
                            FROM email_connections
                            """
                        ).fetchall()
                    ]
                except sqlite3.OperationalError:
                    # The email integration may not have initialized
                    # its database table yet.
                    user_ids = []
                finally:
                    connection.close()

                for uid in user_ids:
                    try:
                        sync_email_user(uid)
                    except Exception as exc:
                        print(
                            f"Email sync failed for user {uid}: {exc}"
                        )

            except Exception as exc:
                print(f"Email worker error: {exc}")

            interval = max(
                60,
                int(os.getenv("EMAIL_SYNC_INTERVAL_SECONDS", "300")),
            )
            _time.sleep(interval)

    threading.Thread(
        target=worker,
        name="email-status-sync",
        daemon=True,
    ).start()