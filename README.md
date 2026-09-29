# Career Agent — Multi-user upgrade

This upgrade changes the local single-profile prototype into a multi-user application.

## What changed

- Account registration and login with email + password.
- Each user receives a private session token.
- Each user has an isolated profile.
- Applications, recruiter emails and activity are isolated per user.
- Jobs remain shared across users, while match scores are calculated per user.
- Gemini resume generation uses the signed-in user's profile.
- Resend API credentials stay on the backend.
- Recruiter emails use the platform's verified sender and set the signed-in user's email as `reply_to`.
- Added draft editing endpoint: `PUT /api/outreach/{email_id}`.
- Existing single-user SQLite databases are upgraded with user_id columns where needed.

## Backend

Replace `backend/app/main.py` with the supplied `main.py`.

From `backend`:

```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m py_compile app\main.py
python -m uvicorn app.main:app --reload --port 8000
```

Create/update `backend/.env` using the example variables. Never put `RESEND_API_KEY` in React/Vite code.

For real recruiter delivery, verify a domain in Resend and use a sender address on that verified domain. The Resend test sender is not intended for arbitrary public recipients.

## Frontend

Replace the existing `frontend` files with the supplied frontend folder, or copy:

- `frontend/package.json`
- `frontend/index.html`
- `frontend/vite.config.js`
- `frontend/src/main.jsx`
- `frontend/src/style.css`

Then:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Public deployment notes

Before exposing this to the public internet, add HTTPS, rate limiting, abuse protection, email verification/anti-spam controls, secure cookies or a managed identity provider, database backups, and a production database. Do not allow arbitrary users to use your email provider as an unrestricted spam relay.

Also note: the current `Approve Application` endpoint only records the user's approval in this application. It does not submit credentials or bypass a third-party job portal. External portal submission requires an official/authorized integration.


## Session recovery in this fixed package

The frontend validates a saved session when it starts. If the backend rejects an expired or invalid token (HTTP 401), the frontend clears that token and returns to the sign-in/create-account screen instead of leaving the dashboard in an invalid state.
