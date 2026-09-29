# HR Job Radar

Watches about 300 Israeli, Israeli-founded and Israel-linked companies, plus Israeli VC portfolio job boards, every 15 minutes. When a **remote-US** HR, People, HRBP, L&D or Talent role opens, you get a phone push and an email within minutes.

It runs on GitHub Actions, which is free for public repos, so it keeps running with your laptop closed.

---

## One-time setup (about 10 minutes)

### 1. Phone alerts (ntfy, free)
1. Install **ntfy** from the App Store or Google Play.
2. Tap **+**, subscribe to topic **`hr-radar-b85c0d56`**, and leave the server as `ntfy.sh`.
   Treat the topic name like a password: anyone who has it can read the alerts.

### 2. Create the repo
1. Go to github.com and sign up or log in.
2. Click **New repository**, name it `hr-job-radar`, and choose **Public**.
   Public repos get unlimited free Actions minutes; private ones would run out at 15-minute checks. Nothing personal goes into the repo, only company names and public job links.
3. Click **Create repository**, then **uploading an existing file**.
   Drag in `scan.py`, `companies.json`, `boards.json`, `requirements.txt`, `README.md` and `.gitignore`, then **Commit changes**.
4. Click **Add file → Create new file**. For the file name, type exactly `.github/workflows/scan.yml`, paste in the contents of the separate `scan.yml` file I sent, and **Commit changes**.
   (Mac Finder hides folders whose names start with a dot, which is why this file is added separately.)

### 3. Secrets
Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `NTFY_TOPIC` | `hr-radar-b85c0d56` |
| `ALERT_EMAIL` *(optional)* | addresses to email, comma-separated, e.g. `nitzanfr1@gmail.com,theshelly1@gmail.com` |
| `SMTP_USER` *(optional)* | the Gmail address the emails come from |
| `SMTP_PASS` *(optional)* | a Gmail **App Password** for that account (Google Account → Security → 2-Step Verification → App passwords), not the normal password |

You can skip the three email secrets. GitHub already emails you about every new-role issue once you set **Watch → All Activity** on the repo (top right).

### 4. Turn it on
1. Go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and **Save**.
2. Open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
3. Click **HR Job Radar → Run workflow**.
   The first run sends one summary of every role that's already open. After that you only hear about new ones.

---

## What you'll receive
* **Phone push** for each new role. Tap it to open the posting.
* **GitHub Issue** for each new role, which GitHub also emails you. Close the issue once it's applied to or rejected, and the Issues tab works as your tracker.
* **`current_matches.md`** in the repo: the live list of every matching role that's open right now.

Ratings: ★★★ core HRBP / People Ops / L&D / Employee Experience · ★★ Talent Acquisition / Recruiting / Rewards · ★ senior stretch (Head of / Director)

## Filters
* **Titles:** HRBP, People Partner, People Ops, HR Generalist or Manager, L&D, Talent Development, Employee Experience or Engagement, OD, HRIS and similar. VP and Chief titles are skipped, and engineering, sales and marketing roles are excluded.
* **Location:** remote in the US only. Hybrid and on-site roles are dropped. Roles marked "Remote" with no country, and US-wide listings, come through tagged "check".

## Maintenance
* **Add a company:** add an entry to `companies.json`. If you don't know which job-board system it uses, set `"ats": "other"` and `"careers_url"`, and the scanner will try to work it out.
* **Change filters:** edit the `CORE` / `SECONDARY` / `EXCLUDE` lists at the top of `scan.py`.
* **Pause it:** Actions → HR Job Radar → ⋯ → Disable workflow.
* **Check it's running:** the Actions tab lists every run. If a company's job board fails, that run's log names it.
* GitHub pauses scheduled workflows in a repo that's had no activity for 60 days. The bot's own commits normally keep it active; if it does get paused, click **Enable** in the Actions tab.
