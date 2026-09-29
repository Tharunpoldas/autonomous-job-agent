
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const API =
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

const tokenKey = "career_agent_token";

const getToken = () => localStorage.getItem(tokenKey) || "";

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API}${path}`, {
    ...options,
    headers,
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    const error = new Error(
      data?.detail?.error?.message ||
        data?.detail?.message ||
        (typeof data?.detail === "string" ? data.detail : "") ||
        `Request failed (${res.status})`
    );
    error.status = res.status;
    throw error;
  }

  return data;
}

function Auth({ onAuth }) {
  const [mode, setMode] = useState("register");
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);

    try {
      const data = await api(
        mode === "register"
          ? "/api/auth/register"
          : "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify(form),
        }
      );

      localStorage.setItem(tokenKey, data.token);
      onAuth(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="brand">Career Agent</div>
        <p className="muted">
          AI-powered job search, resume customization and recruiter
          outreach.
        </p>

        <div className="tabs">
          <button
            className={mode === "register" ? "active" : ""}
            onClick={() => setMode("register")}
          >
            Create account
          </button>
          <button
            className={mode === "login" ? "active" : ""}
            onClick={() => setMode("login")}
          >
            Sign in
          </button>
        </div>

        <form onSubmit={submit}>
          {mode === "register" && (
            <label>
              Full name
              <input
                required
                value={form.name}
                onChange={(e) =>
                  setForm({ ...form, name: e.target.value })
                }
                placeholder="Your name"
              />
            </label>
          )}

          <label>
            Email
            <input
              required
              type="email"
              value={form.email}
              onChange={(e) =>
                setForm({ ...form, email: e.target.value })
              }
              placeholder="you@example.com"
            />
          </label>

          <label>
            Password
            <input
              required
              type="password"
              minLength={8}
              value={form.password}
              onChange={(e) =>
                setForm({ ...form, password: e.target.value })
              }
              placeholder="At least 8 characters"
            />
          </label>

          {error && <div className="error">{error}</div>}

          <button className="primary full" disabled={busy}>
            {busy
              ? "Please wait..."
              : mode === "register"
              ? "Create my account"
              : "Sign in"}
          </button>
        </form>

        <p className="tiny">
          Your account data is isolated from other users. Email
          delivery uses the configured sender.
        </p>
      </div>
    </div>
  );
}

function App() {
  const [auth, setAuth] = useState(null);
  const [profile, setProfile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [integrations, setIntegrations] = useState(null);
  const [events, setEvents] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [resume, setResume] = useState(null);
  const [emailDraft, setEmailDraft] = useState(null);
  const [tab, setTab] = useState("jobs");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [emailConnections, setEmailConnections] = useState([]);
  const [checkingSession, setCheckingSession] = useState(
    Boolean(getToken())
  );

  async function loadAll() {
    setLoading(true);
    setError("");

    try {
      const [me, j, d, i, e, ec] = await Promise.all([
        api("/api/account/me"),
        api("/api/jobs"),
        api("/api/dashboard"),
        api("/api/integrations"),
        api("/api/events"),
        api("/api/email/connections"),
      ]);

      setProfile(me.profile);
      setJobs(Array.isArray(j) ? j : j.jobs || []);
      setDashboard(d);
      setIntegrations(i);
      setEvents(Array.isArray(e) ? e : e.events || []);
      setEmailConnections(Array.isArray(ec) ? ec : []);
    } catch (err) {
      if (err.status === 401) {
        localStorage.removeItem(tokenKey);
        setAuth(null);
        setProfile(null);
        setError("");
        setMessage("");
      } else {
        setError(
          err.message || "Unable to load your account. Please try again."
        );
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (getToken()) {
      loadAll().finally(() => setCheckingSession(false));
    } else {
      setCheckingSession(false);
    }
  }, []);

  // OAuth callback handling
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);

    if (params.has("email_connected")) {
      setMessage(
        `${params.get("email_connected")} email tracking enabled`
      );

      window.history.replaceState(
        {},
        "",
        window.location.pathname
      );

      loadAll();
    } else if (params.has("email_error")) {
      setError(
        "Email authorization was not completed. Email tracking remains off."
      );

      window.history.replaceState(
        {},
        "",
        window.location.pathname
      );
    }
  }, []);

  // Manually connect an email provider.
  async function connectEmail(provider) {
    setError("");
    setMessage(`Connecting to ${provider}...`);

    try {
      const result = await api(
        `/api/email/connect/${provider}`
      );

      if (!result.authorization_url) {
        throw new Error("Authorization URL was not returned.");
      }

      // Navigate to Google's or Microsoft's authorization page.
      window.location.assign(result.authorization_url);
    } catch (err) {
      setMessage("");
      setError(err.message || "Unable to start email authorization.");
    }
  }

  async function saveProfile() {
    try {
      const p = await api("/api/profile", {
        method: "PUT",
        body: JSON.stringify(profile),
      });

      setProfile(p);
      setMessage("Profile saved");
    } catch (e) {
      setError(e.message);
    }
  }

  async function syncJobs() {
    try {
      setMessage("Syncing live jobs...");

      const r = await api("/api/jobs/sync", {
        method: "POST",
      });

      setMessage(`Synced ${r.fetched ?? 0} jobs`);
      await loadAll();
    } catch (e) {
      setError(e.message);
    }
  }

  async function makeResume(job) {
    if (!job) {
      throw new Error("Please select a job.");
    }

    try {
      setSelectedJob(job);
      setMessage("Generating tailored resume...");

      const r = await api(`/api/resume/${job.id}`, {
        method: "POST",
      });

      setResume(r);
      setMessage("Tailored resume generated");

      return r;
    } catch (e) {
      setError(e.message);
      throw e;
    }
  }

  async function prepareApplication(job) {
    if (!job) return;

    try {
      const r = await api("/api/applications", {
        method: "POST",
        body: JSON.stringify({
          job_id: job.id,
          mode: "approval",
          resume_version: resume?.version || "ai-generated",
          answers: {},
        }),
      });

      setMessage(
        r.duplicate
          ? "Application already exists"
          : "Application prepared for approval"
      );

      await loadAll();
    } catch (e) {
      setError(e.message);
    }
  }

  async function createEmail(job) {
    const recruiter = window.prompt("Recruiter email address:");
    if (!recruiter) return;

    const name =
      window.prompt("Recruiter name (optional):", "Hiring Team") ||
      "Hiring Team";

    try {
      const r = await api("/api/outreach", {
        method: "POST",
        body: JSON.stringify({
          job_id: job.id,
          recruiter_email: recruiter,
          recruiter_name: name,
        }),
      });

      setEmailDraft(r);
      setTab("email");
      setMessage("Email draft created. Review it before sending.");

      await loadAll();
    } catch (e) {
      setError(e.message);
    }
  }

  async function sendEmail() {
    if (!emailDraft) return;

    try {
      const saved = await api(`/api/outreach/${emailDraft.id}`, {
        method: "PUT",
        body: JSON.stringify({
          job_id: emailDraft.job_id,
          recruiter_email: emailDraft.to,
          recruiter_name: "Hiring Team",
          subject: emailDraft.subject,
          body: emailDraft.body,
        }),
      });

      const r = await api(`/api/outreach/${saved.id}/send`, {
        method: "POST",
      });

      setEmailDraft(r.email || r);
      setMessage("Email accepted by the configured sender.");
      await loadAll();
    } catch (e) {
      setError(e.message);
    }
  }

  async function approveApplication(app) {
    try {
      await api(`/api/applications/${app.id}/approve`, {
        method: "POST",
      });

      setMessage("Application approved");
      await loadAll();
    } catch (e) {
      setError(e.message);
    }
  }

  function logout() {
    localStorage.removeItem(tokenKey);
    setAuth(null);
    setProfile(null);
    setJobs([]);
    setDashboard(null);
    setIntegrations(null);
    setEvents([]);
    setEmailConnections([]);
    setResume(null);
    setSelectedJob(null);
    setEmailDraft(null);
    setTab("jobs");
  }

  if (checkingSession) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <div className="brand">Career Agent</div>
          <p className="muted">Checking your session...</p>
        </div>
      </div>
    );
  }

  if (!getToken() && !auth) {
    return (
      <Auth
        onAuth={(data) => {
          setAuth(data);
          loadAll();
        }}
      />
    );
  }

  const relevant = jobs.filter((j) => j.match?.score >= 60).length;

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <div className="brand">Career Agent</div>
          <div className="subtitle">
            One account • private workspace • AI job automation
          </div>
        </div>

        <div className="header-actions">
          <span className="pill green">Online</span>
          <span className="pill">{profile?.email}</span>
          <button className="ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <div className="notice">
        <b>Multi-user mode:</b> every visitor signs in and gets an
        isolated profile, applications, outreach history and dashboard.
        Email sending uses the configured platform sender.
      </div>

      <div className="tiny global">
        Email tracking runs automatically for authorized mailboxes.
        Background check interval: 5 minutes.
      </div>

      {error && (
        <div className="error global">
          {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}

      {message && <div className="success global">{message}</div>}

      <nav>
        {[
          ["jobs", "Jobs"],
          ["resume", "Resume"],
          ["profile", "My Profile"],
          ["applications", "Applications"],
          ["email", "Recruiter Outreach"],
          ["activity", "Activity"],
        ].map(([key, value]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {value}
          </button>
        ))}
      </nav>

      <main>
        {tab === "jobs" && (
          <>
            <section className="hero">
              <div>
                <h1>Find and prepare for your next role</h1>
                <p>
                  Jobs are shared globally; your match score and AI
                  resume are calculated from your private profile.
                </p>
              </div>

              <button
                className="primary"
                onClick={syncJobs}
                disabled={loading}
              >
                {loading ? "Syncing..." : "Sync live jobs"}
              </button>
            </section>

            <div className="stats">
              <Stat
                n={dashboard?.jobs_discovered || 0}
                t="Jobs discovered"
              />
              <Stat n={relevant} t="Relevant jobs" />
              <Stat
                n={dashboard?.applications || 0}
                t="Applications"
              />
              <Stat
                n={dashboard?.emails_sent || 0}
                t="Emails sent"
              />
              <Stat
                n={dashboard?.emails_failed || 0}
                t="Failed emails"
              />
            </div>

            <div className="grid">
              {jobs.slice(0, 30).map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  onResume={(selected) => {
                    setSelectedJob(selected);
                    setTab("resume");
                    makeResume(selected).catch(() => {});
                  }}
                  onApply={prepareApplication}
                  onEmail={createEmail}
                />
              ))}
            </div>

            {!jobs.length && (
              <div className="card empty-state">
                No jobs available. Use Sync live jobs to fetch jobs.
              </div>
            )}
          </>
        )}

        {tab === "resume" && (
          <ResumePanel
            jobs={jobs}
            resume={resume}
            selectedJob={selectedJob}
            onGenerate={makeResume}
            onPrepare={prepareApplication}
          />
        )}

        {tab === "profile" && (
          <Profile
            profile={profile}
            setProfile={setProfile}
            onSave={saveProfile}
          />
        )}

        {tab === "applications" && (
          <Applications onApprove={approveApplication} />
        )}

        {tab === "email" && (
          <EmailPanel
            draft={emailDraft}
            setDraft={setEmailDraft}
            onSend={sendEmail}
            profile={profile}
            connections={emailConnections}
            onConnect={connectEmail}
          />
        )}

        {tab === "activity" && <Activity events={events} />}
      </main>

      <footer>
        Career Agent • Multi-user architecture • Secrets stay on the backend
      </footer>
    </div>
  );
}

function ResumePanel({
  jobs,
  resume,
  selectedJob,
  onGenerate,
  onPrepare,
}) {
  const [jobId, setJobId] = useState(
    selectedJob?.id ? String(selectedJob.id) : ""
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (selectedJob?.id) {
      setJobId(String(selectedJob.id));
    }
  }, [selectedJob?.id]);

  const chosenJob = jobs.find(
    (job) => String(job.id) === String(jobId)
  );

  const currentResume =
    resume && String(resume.job_id) === String(jobId)
      ? resume
      : null;

  const data = currentResume?.resume;

  async function generate() {
    if (!chosenJob || busy) return;

    setBusy(true);
    setError("");

    try {
      await onGenerate(chosenJob);
    } catch (e) {
      setError(e.message || "Resume generation failed");
    } finally {
      setBusy(false);
    }
  }

  function downloadLatex() {
    if (!currentResume?.latex) {
      setError("LaTeX source is not available.");
      return;
    }

    const blob = new Blob([currentResume.latex], {
      type: "application/x-tex;charset=utf-8",
    });

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = "tailored_resume.tex";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function printResume() {
    if (!currentResume || !data) {
      setError("Generate a resume before printing.");
      return;
    }

    setError("");
    window.print();
  }

  return (
    <section className="card resume-panel">
      <h2>AI Resume Generator</h2>
      <p className="muted">
        Generate a complete, ATS-friendly resume tailored to a job
        using your saved profile.
      </p>

      <label>
        Select a job
        <select
          value={jobId}
          onChange={(e) => {
            setJobId(e.target.value);
            setError("");
          }}
        >
          <option value="">Choose a job</option>
          {jobs.map((job) => (
            <option key={job.id} value={String(job.id)}>
              {job.title} — {job.company}
            </option>
          ))}
        </select>
      </label>

      <div className="actions no-print">
        <button
          className="primary"
          disabled={!chosenJob || busy}
          onClick={generate}
        >
          {busy ? "Generating..." : "Generate Resume"}
        </button>

        <button disabled={!data} onClick={printResume}>
          Print / Save as PDF
        </button>

        <button
          disabled={!currentResume?.latex}
          onClick={downloadLatex}
        >
          Download LaTeX
        </button>

        <button
          disabled={!chosenJob}
          onClick={() => onPrepare(chosenJob)}
        >
          Prepare Application
        </button>
      </div>

      {error && <div className="error no-print">{error}</div>}

      {currentResume && data && (
        <>
          <div className="resume-toolbar no-print">
            <h3>Generated Resume</h3>
            <span className="score">
              Match score: {currentResume.match?.score ?? "N/A"}%
            </span>
          </div>

          <article className="resume-paper" id="resume-paper">
            <header className="resume-heading">
              <div>
                <h1>{data.name || "Candidate"}</h1>
                {data.email && <p>{data.email}</p>}
                {data.phone && <p>{data.phone}</p>}
                {data.location && <p>{data.location}</p>}
              </div>
            </header>

            <section className="resume-section">
              <h2>Professional Summary</h2>
              <p>{data.summary || "Not provided"}</p>
            </section>

            <section className="resume-section">
              <h2>Technical Skills</h2>
              <p>
                {(data.skills || []).join(" • ") || "Not provided"}
              </p>
            </section>

            {(data.projects || []).length > 0 && (
              <section className="resume-section">
                <h2>Projects</h2>
                <ul>
                  {data.projects.map((project, index) => (
                    <li key={`${project}-${index}`}>{project}</li>
                  ))}
                </ul>
              </section>
            )}

            {data.education && (
              <section className="resume-section">
                <h2>Education</h2>
                <p>{data.education}</p>
              </section>
            )}

            {data.experience &&
              !["entry level", "fresher", "none"].includes(
                data.experience.trim().toLowerCase()
              ) && (
                <section className="resume-section">
                  <h2>Experience</h2>
                  <p>{data.experience}</p>
                </section>
              )}
          </article>

          <p className="muted no-print">
            Click Print / Save as PDF, then select Save as PDF in
            your browser's print window.
          </p>
        </>
      )}
    </section>
  );
}

function Stat({ n, t }) {
  return (
    <div className="stat">
      <b>{n}</b>
      <span>{t}</span>
    </div>
  );
}

function JobCard({ job, onResume, onApply, onEmail }) {
  return (
    <article className="job-card">
      <div className="job-top">
        <div>
          <h3>{job.title}</h3>
          <b>{job.company}</b>
          <div className="muted">
            {job.location} • {job.mode} • {job.source}
          </div>
        </div>

        <span className="match">
          {job.match?.score || 0}% match
        </span>
      </div>

      <p>{job.description}</p>

      <div className="chips">
        {(job.match?.matched_skills || [])
          .slice(0, 6)
          .map((skill) => (
            <span key={skill}>{skill}</span>
          ))}
      </div>

      <div className="actions">
        <button onClick={() => onResume(job)}>AI Resume</button>
        <button onClick={() => onApply(job)}>
          Prepare Application
        </button>
        <button onClick={() => onEmail(job)}>
          Recruiter Email
        </button>
        {job.url && (
          <a href={job.url} target="_blank" rel="noreferrer">
            View job
          </a>
        )}
      </div>
    </article>
  );
}

function Profile({ profile, setProfile, onSave }) {
  if (!profile) {
    return <div className="card">Loading profile...</div>;
  }

  const set = (key, value) =>
    setProfile({ ...profile, [key]: value });

  return (
    <section className="card">
      <h2>My Profile</h2>
      <p className="muted">
        This information is private to your account and is used for
        matching and AI resume generation.
      </p>

      <div className="form-grid">
        <label>
          Name
          <input
            value={profile.name || ""}
            onChange={(e) => set("name", e.target.value)}
          />
        </label>

        <label>
          Account email
          <input value={profile.email || ""} disabled />
        </label>

        <label>
          Target roles
          <input
            value={(profile.titles || []).join(", ")}
            onChange={(e) =>
              set(
                "titles",
                e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean)
              )
            }
          />
        </label>

        <label>
          Locations
          <input
            value={(profile.locations || []).join(", ")}
            onChange={(e) =>
              set(
                "locations",
                e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean)
              )
            }
          />
        </label>

        <label>
          Experience
          <input
            value={profile.experience || ""}
            onChange={(e) => set("experience", e.target.value)}
          />
        </label>

        <label>
          Salary
          <input
            value={profile.salary || ""}
            onChange={(e) => set("salary", e.target.value)}
          />
        </label>

        <label className="wide">
          Skills
          <input
            value={(profile.skills || []).join(", ")}
            onChange={(e) =>
              set(
                "skills",
                e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean)
              )
            }
          />
        </label>

        <label className="wide">
          Projects
          <input
            value={(profile.projects || []).join(", ")}
            onChange={(e) =>
              set(
                "projects",
                e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean)
              )
            }
          />
        </label>

        <label className="wide">
          Education
          <input
            value={profile.education || ""}
            onChange={(e) => set("education", e.target.value)}
          />
        </label>
      </div>

      <button className="primary" onClick={onSave}>
        Save profile
      </button>
    </section>
  );
}

function Applications({ onApprove }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/applications")
      .then((data) => setItems(Array.isArray(data) ? data : []))
      .catch((e) => setError(e.message));
  }, []);

  return (
    <section>
      <div className="section-head">
        <div>
          <h2>My Applications</h2>
          <p className="muted">
            Only your applications are visible here.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="list">
        {items.length ? (
          items.map((app) => (
            <div className="row" key={app.id}>
              <div>
                <b>{app.title}</b>
                <div className="muted">
                  {app.company} • {app.portal}
                </div>
              </div>

              <span className={`status ${app.status}`}>
                {app.status}
              </span>

              {app.status === "pending_approval" && (
                <button
                  className="primary small"
                  onClick={() => onApprove(app)}
                >
                  Approve
                </button>
              )}
            </div>
          ))
        ) : (
          <div className="card">No applications yet.</div>
        )}
      </div>
    </section>
  );
}

function EmailPanel({
  draft,
  setDraft,
  onSend,
  profile,
  connections,
  onConnect,
}) {
  const [items, setItems] = useState([]);

  const gmailConnected = connections.some(
    (c) => c.provider === "gmail" && c.connected
  );

  const outlookConnected = connections.some(
    (c) => c.provider === "outlook" && c.connected
  );

  useEffect(() => {
    api("/api/outreach")
      .then((data) => setItems(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, [draft]);

  return (
    <section>
      <div className="section-head">
        <div>
          <h2>Recruiter Outreach</h2>
          <p className="muted">
            Connect your own mailbox to monitor recruiter replies.
            Create a draft, review it, then send manually.
          </p>
        </div>
      </div>

      {/* Email account connections */}
      <div className="card email-connect-card">
        <h2>Connect your email account</h2>
        <p className="muted">
          Each member can connect their own Google or Microsoft
          account. You will be redirected to the provider to
          authorize access.
        </p>

        <div className="email-provider-list">
          <div className="email-provider">
            <div className="email-provider-info">
              <div className="email-provider-icon google-icon">
                G
              </div>
              <div>
                <b>Google Gmail</b>
                <div className="muted">
                  {gmailConnected ? "Connected" : "Not connected"}
                </div>
              </div>
            </div>

            {gmailConnected ? (
              <span className="pill green">Connected</span>
            ) : (
              <button
                className="primary"
                onClick={() => onConnect("gmail")}
              >
                Connect with Google
              </button>
            )}
          </div>

          <div className="email-provider">
            <div className="email-provider-info">
              <div className="email-provider-icon outlook-icon">
                O
              </div>
              <div>
                <b>Microsoft Outlook</b>
                <div className="muted">
                  {outlookConnected ? "Connected" : "Not connected"}
                </div>
              </div>
            </div>

            {outlookConnected ? (
              <span className="pill green">Connected</span>
            ) : (
              <button
                className="primary"
                onClick={() => onConnect("outlook")}
              >
                Connect with Outlook
              </button>
            )}
          </div>
        </div>

        <p className="tiny">
          Email monitoring requires your authorization. Connecting
          a mailbox does not automatically send emails.
        </p>
      </div>

      {draft && (
        <div className="card">
          <h3>Review recruiter email</h3>

          <label>
            To
            <input
              value={draft.to || ""}
              onChange={(e) =>
                setDraft({ ...draft, to: e.target.value })
              }
            />
          </label>

          <label>
            Subject
            <input
              value={draft.subject || ""}
              onChange={(e) =>
                setDraft({ ...draft, subject: e.target.value })
              }
            />
          </label>

          <label>
            Message
            <textarea
              rows="10"
              value={draft.body || ""}
              onChange={(e) =>
                setDraft({ ...draft, body: e.target.value })
              }
            />
          </label>

          <div className="actions">
            <button
              className="primary"
              onClick={onSend}
              disabled={draft.status === "sent"}
            >
              {draft.status === "sent" ? "Sent" : "Approve & Send"}
            </button>
          </div>
        </div>
      )}

      <div className="list">
        {items.map((item) => (
          <div className="row" key={item.id}>
            <div>
              <b>{item.subject}</b>
              <div className="muted">To: {item.to}</div>
            </div>
            <span className={`status ${item.status}`}>
              {item.status}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Activity({ events }) {
  return (
    <section>
      <h2>Recent Activity</h2>
      <div className="list">
        {events.length ? (
          events.map((event, index) => (
            <div className="row" key={event.id || index}>
              <div>
                <b>{event.kind}</b>
                <div>{event.message}</div>
              </div>
              <span className="muted">
                {event.time
                  ? new Date(event.time).toLocaleString()
                  : ""}
              </span>
            </div>
          ))
        ) : (
          <div className="card">No activity yet.</div>
        )}
      </div>
    </section>
  );
}

createRoot(document.getElementById("root")).render(<App />);