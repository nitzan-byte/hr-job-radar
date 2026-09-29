#!/usr/bin/env python3
"""
HR Job Radar — scans Israeli / Israeli-founded / Israel-linked companies for
REMOTE-US HR / People / L&D roles and alerts on anything new.

Sources
  * companies.json  – ~290 companies, each with its ATS (Greenhouse, Ashby, Lever,
                      Workable, SmartRecruiters, Workday, Comeet, or "other" = auto-detect)
  * boards.json     – Israeli VC portfolio job boards (Getro) covering hundreds more startups

State
  * seen.json          – ids already alerted (committed back by the GitHub Action)
  * current_matches.md – live list of every matching open role right now

Alerts (all optional, configured with env vars / GitHub secrets)
  * NTFY_TOPIC       – instant phone push via the free ntfy app
  * GITHUB_TOKEN + GITHUB_REPOSITORY – opens a GitHub Issue per new role (GitHub emails you)
  * SMTP_USER / SMTP_PASS / ALERT_EMAIL – email via Gmail app password
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests

ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (HR-Job-Radar; personal job alert bot)",
      "Accept": "application/json, text/html;q=0.9"}
TIMEOUT = 25
S = requests.Session()
S.headers.update(UA)

# ----------------------------------------------------------------------------
# 1. MATCHING RULES  (tuned to the candidate's profile: HRBP / People Ops / L&D / TA,
#    mid-level, remote within the US only)
# ----------------------------------------------------------------------------
CORE = [  # score 3 — her sweet spot
    r"\bhr ?bp\b", r"\bhr business partner", r"human resources business partner",
    r"\bpeople (business )?partner", r"people & culture partner", r"people and culture partner",
    r"\bpeople (operations|ops)\b", r"\bhr (operations|ops)\b", r"\bhr generalist",
    r"human resources (generalist|manager|specialist|coordinator|partner)",
    r"\bhr (manager|specialist|coordinator|partner|lead)\b", r"\bpeople (manager|specialist|coordinator|generalist|lead|program)",
    r"\bpeople experience", r"employee experience", r"employee engagement",
    r"learning (&|and) development", r"\bl&d\b", r"\blearning (partner|manager|specialist|program|experience|lead|designer)",
    r"talent (development|management)", r"organi[sz]ational (development|effectiveness)",
    r"\bculture (manager|partner|lead|program)", r"onboarding (manager|specialist|lead|coordinator)",
    r"employee relations", r"\bhris\b", r"\benablement (manager|partner)\b.*people",
    r"\bpeople & culture\b", r"\bpeople and culture\b", r"\bhuman resources\b",
    r"\bhead of (people|hr|talent|learning|culture)",
]
SECONDARY = [  # score 2 — relevant, less central
    r"talent acquisition", r"\brecruit(er|ing|ment)\b", r"people analytics",
    r"\bbenefits\b", r"total rewards", r"\bcompensation\b", r"workplace experience",
]
EXCLUDE = [
    r"\bengineer", r"\bdeveloper", r"software", r"\bsales\b", r"account executive",
    r"\bmarketing\b", r"data scien", r"\bintern\b", r"\binternship", r"technical recruiter.*(contract)?\b(emea|apac)",
    r"product manager", r"\bsolutions?\b", r"\bsecurity\b(?!.*people)",
]
TOO_SENIOR = [r"\bvp\b", r"vice president", r"\bchief\b", r"\bsvp\b", r"\bevp\b", r"\bcpo\b", r"\bchro\b"]
STRETCH = [r"\bhead of\b", r"\bdirector\b", r"\bprincipal\b"]


def title_score(title: str):
    t = title.lower()
    if any(re.search(p, t) for p in TOO_SENIOR):
        return 0, "too senior"
    if any(re.search(p, t) for p in EXCLUDE):
        return 0, "not HR"
    score, why = 0, ""
    for p in CORE:
        if re.search(p, t):
            score, why = 3, "core HR/People/L&D"
            break
    if not score:
        for p in SECONDARY:
            if re.search(p, t):
                score, why = 2, "talent/recruiting/rewards"
                break
    if score and any(re.search(p, t) for p in STRETCH):
        score -= 1
        why += " (stretch: senior)"
    return score, why


US_STATES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
             "hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
             "michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
             "new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
             "rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
             "west virginia|wisconsin|wyoming")
US_RX = re.compile(r"(united states|\busa\b|\bu\.s\.a?\b|\bus\b|\bamericas?\b|north america|anywhere|"
                   r"\b(" + US_STATES + r")\b|,\s?(ny|ca|tx|ma|fl|ga|nc|sc|il|wa|co|nj|va|pa|az|ut|or|mi|oh|tn|md|mn)\b)")
NON_US_RX = re.compile(r"(israel|tel aviv|herzliya|haifa|jerusalem|petah|ra'?anana|yokneam|\buk\b|united kingdom|"
                       r"london|england|ireland|dublin|\bemea\b|europe|germany|berlin|france|paris|spain|madrid|"
                       r"portugal|lisbon|poland|warsaw|netherlands|amsterdam|india|bangalore|bengaluru|"
                       r"\bapac\b|australia|sydney|singapore|japan|tokyo|canada|toronto|vancouver|brazil|mexico|"
                       r"latam|argentina|philippines|ukraine|romania|bulgaria|czech|serbia|cyprus|greece|italy)")


def remote_us(loc_text: str, is_remote=None, country=None):
    """Return (ok, note). Remote-US only; hybrid/on-site rejected."""
    t = (loc_text or "").lower()
    c = (country or "").lower()
    remote = bool(is_remote) or "remote" in t or "work from home" in t or "wfh" in t or "virtual" in t
    us_hit = bool(US_RX.search(t)) or c in ("us", "usa", "united states", "united states of america")
    non_us = bool(NON_US_RX.search(t)) or (c and c not in ("us", "usa", "united states", "united states of america"))
    if "hybrid" in t and "remote" not in t:
        return False, "hybrid"
    if remote and us_hit:
        return True, "Remote – US"
    if remote and not non_us:
        return True, "Remote (country not stated – check)"
    if not remote and re.fullmatch(r"\s*(united states|usa|us|u\.s\.)( of america)?\s*", t):
        return True, "US-wide listing (likely remote – check)"
    return False, "not remote-US"


# ----------------------------------------------------------------------------
# 2. FETCHERS — each yields dicts {id,title,location,url,company,posted,remote_flag,country,dept}
# ----------------------------------------------------------------------------
def get_json(url, **kw):
    r = S.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def f_greenhouse(c):
    slug = c["slug"]
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    for j in data.get("jobs", []):
        yield dict(id=f"gh:{slug}:{j['id']}", title=j["title"], location=(j.get("location") or {}).get("name", ""),
                   url=j.get("absolute_url"), posted=j.get("first_published") or j.get("updated_at"))


def f_ashby(c):
    slug = c["slug"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        remote = j.get("isRemote") or (j.get("workplaceType") or "").lower() == "remote"
        if (j.get("workplaceType") or "").lower() == "hybrid":
            remote = False
        yield dict(id=f"ashby:{slug}:{j['id']}", title=j["title"], location=" | ".join(l for l in locs if l),
                   url=j.get("jobUrl"), posted=j.get("publishedAt"), remote_flag=remote, country=country,
                   dept=j.get("department"))


def f_lever(c):
    slug = c["slug"]
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    for j in data:
        cat = j.get("categories") or {}
        loc = " | ".join(filter(None, [cat.get("location")] + (cat.get("allLocations") or [])))
        wt = (j.get("workplaceType") or "").lower()
        yield dict(id=f"lever:{slug}:{j['id']}", title=j.get("text", ""), location=loc, url=j.get("hostedUrl"),
                   posted=dt.datetime.utcfromtimestamp(j.get("createdAt", 0) / 1000).isoformat() if j.get("createdAt") else None,
                   remote_flag=(wt == "remote"), country=j.get("country"), dept=cat.get("team"))


def f_workable(c):
    slug = c["slug"]
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    for j in data.get("jobs", []):
        loc = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        yield dict(id=f"workable:{slug}:{j.get('shortcode')}", title=j.get("title", ""), location=loc,
                   url=j.get("url") or j.get("shortlink"), posted=j.get("published_on"),
                   remote_flag=j.get("telecommuting"), country=j.get("country"))


def f_smartrecruiters(c):
    slug = c["slug"]
    offset = 0
    while True:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        for j in data.get("content", []):
            l = j.get("location") or {}
            loc = ", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")]))
            yield dict(id=f"sr:{slug}:{j['id']}", title=j.get("name", ""), location=loc,
                       url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", posted=j.get("releasedDate"),
                       remote_flag=l.get("remote"), country=l.get("country"))
        offset += 100
        if offset >= data.get("totalFound", 0) or offset > 1500:
            break


WD_QUERIES = ["human resources", "HR business partner", "people partner", "people operations",
              "learning development", "talent development", "employee experience", "HR generalist"]


def f_workday(c):
    w = c["workday"]
    base = f"https://{w['host']}/wday/cxs/{w['tenant']}/{w['site']}"
    seen = set()
    for q in WD_QUERIES:
        r = S.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q},
                   timeout=TIMEOUT, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        for j in r.json().get("jobPostings", []):
            path = j.get("externalPath")
            if not path or path in seen:
                continue
            seen.add(path)
            loc = j.get("locationsText", "")
            # Only pay for a detail call when it could be a US-remote HR role
            if title_score(j.get("title", ""))[0] and (re.search(r"locations", loc, re.I) or "remote" in loc.lower()
                                                        or US_RX.search(loc.lower())):
                try:
                    d = get_json(f"{base}{path}").get("jobPostingInfo", {})
                    loc = " | ".join(filter(None, [d.get("location")] + (d.get("additionalLocations") or []) +
                                            [d.get("remoteType") or ""]))
                    if "remote" in (d.get("jobDescription") or "").lower()[:1500] and "remote" not in loc.lower():
                        loc += " | (description mentions remote)"
                except Exception:
                    pass
            yield dict(id=f"wd:{w['tenant']}:{path.rsplit('_', 1)[-1]}", title=j.get("title", ""), location=loc,
                       url=f"https://{w['host']}/{w['site']}{path}", posted=j.get("postedOn"))


COMEET_RX = re.compile(r"COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\]);\s*\n", re.S)


def f_comeet(c):
    url = c["url"]
    page = S.get(url, timeout=TIMEOUT).text
    m = COMEET_RX.search(page)
    if not m:
        return
    for j in json.loads(m.group(1)):
        l = j.get("location") or {}
        loc = ", ".join(filter(None, [l.get("name"), l.get("city"), l.get("state"), l.get("country")]))
        wt = (j.get("workplace_type") or "").lower()
        yield dict(id=f"comeet:{j.get('uid')}", title=j.get("name", ""), location=loc + (f" | {wt}" if wt else ""),
                   url=j.get("url_active_page") or j.get("url_comeet_hosted_page"), posted=j.get("time_updated"),
                   remote_flag=(wt == "remote"), country=l.get("country"), dept=j.get("department"))


# auto-detect the ATS behind a plain careers page
DETECT = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("comeet", re.compile(r"(https://www\.comeet\.com/jobs/[A-Za-z0-9_.-]+/[0-9A-F]{2}\.[0-9A-F]{3})")),
    ("workday", re.compile(r"https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
]
BAD_SLUGS = {"embed", "js", "v1", "jobs", "api"}


def f_other(c):
    url = c.get("careers_url")
    if not url:
        return
    page = S.get(url, timeout=TIMEOUT, headers={"Accept": "text/html"}).text
    for ats, rx in DETECT:
        m = rx.search(page)
        if not m:
            continue
        cc = dict(c)
        if ats == "workday":
            cc["workday"] = {"host": f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com", "tenant": m.group(1),
                             "site": m.group(3)}
        elif ats == "comeet":
            cc["url"] = m.group(1)
        else:
            if m.group(1).lower() in BAD_SLUGS:
                continue
            cc["slug"] = m.group(1)
        yield from FETCH[ats](cc)
        return


def f_getro_board(b):
    cid = b.get("collection_id")
    if not cid:
        page = S.get(b["url"], timeout=TIMEOUT, headers={"Accept": "text/html"}).text
        m = re.search(r'"network":\{"id":"?(\d+)', page)
        if not m:
            raise RuntimeError("no collection id")
        cid = m.group(1)
    for mode in ("remote", None):
        filters = {"job_functions": ["People & HR"]}
        if mode:
            filters["work_mode"] = [mode]
        for page in range(0, 15):
            r = S.post(f"https://api.getro.com/api/v2/collections/{cid}/search/jobs",
                       json={"hitsPerPage": 20, "page": page, "query": "", "filters": filters},
                       headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            jobs = (r.json().get("results") or {}).get("jobs") or []
            for j in jobs:
                org = (j.get("organization") or {}).get("name", "?")
                yield dict(id=f"getro:{j['id']}", title=j.get("title", ""), company=org,
                           location=" | ".join(j.get("locations") or []) + f" | {j.get('work_mode', '')}",
                           url=j.get("url"), remote_flag=(j.get("work_mode") == "remote"),
                           posted=dt.datetime.utcfromtimestamp(j["created_at"]).isoformat() if j.get("created_at") else None,
                           israel_link=f"portfolio of {b['name'].replace(' Job Board', '')}")
            if len(jobs) < 20:
                break


FETCH = dict(greenhouse=f_greenhouse, ashby=f_ashby, lever=f_lever, workable=f_workable,
             smartrecruiters=f_smartrecruiters, workday=f_workday, comeet=f_comeet, other=f_other)


# ----------------------------------------------------------------------------
# 3. RUN
# ----------------------------------------------------------------------------
def scan_company(c):
    out = []
    try:
        for j in FETCH[c["ats"]](c):
            j.setdefault("company", c["name"])
            j.setdefault("israel_link", c.get("israel_link", ""))
            out.append(j)
        return c["name"], out, None
    except Exception as e:  # noqa
        return c["name"], out, f"{type(e).__name__}: {str(e)[:120]}"


def scan_board(b):
    try:
        return b["name"], list(f_getro_board(b)), None
    except Exception as e:  # noqa
        return b["name"], [], f"{type(e).__name__}: {str(e)[:120]}"


def evaluate(jobs):
    matches = {}
    for j in jobs:
        s, why = title_score(j.get("title", ""))
        if s < 1 and j.get("dept") and re.search(r"\b(people|hr|human resources|talent)\b", str(j["dept"]).lower()) \
                and not any(re.search(p, j["title"].lower()) for p in EXCLUDE + TOO_SENIOR):
            s, why = 2, "People/HR department"
        if s < 1:
            continue
        ok, note = remote_us(j.get("location", ""), j.get("remote_flag"), j.get("country"))
        if not ok:
            continue
        j["score"], j["why"], j["where"] = s, why, note
        key = (j["company"].lower(), re.sub(r"\W+", "", j["title"].lower()))
        if key not in matches or matches[key]["id"] > j["id"]:
            matches[key] = j
    return sorted(matches.values(), key=lambda x: (-x["score"], x["company"].lower()))


# ---------------------------------------------------------------- notifications
def push_ntfy(title, body, click=None, prio="high"):
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return
    h = {"Title": title.encode("utf-8"), "Priority": prio, "Tags": "briefcase"}
    if click:
        h["Click"] = click
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=h, timeout=15)
    except Exception as e:
        print("ntfy failed", e)


def gh_issue(title, body, labels):
    tok, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not (tok and repo):
        return
    try:
        requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=20,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"title": title[:250], "body": body, "labels": labels})
    except Exception as e:
        print("issue failed", e)


def email(subject, body_html):
    u, p, to = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"), os.getenv("ALERT_EMAIL")
    if not (u and p and to):
        return
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, u, to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(u, p)
            s.sendmail(u, [a.strip() for a in to.split(",")], msg.as_string())
    except Exception as e:
        print("email failed", e)


def stars(s):
    return {3: "★★★", 2: "★★", 1: "★"}.get(s, "")


def line_md(j):
    return (f"- {stars(j['score'])} **{j['title']}** — {j['company']} · {j['where']}  \n"
            f"  {j.get('israel_link', '')} · posted {str(j.get('posted') or '?')[:10]} · [open posting]({j['url']})")


def row_html(j):
    return (f"<li><b>{html.escape(j['title'])}</b> — {html.escape(j['company'])} "
            f"<i>({html.escape(j['where'])})</i> {stars(j['score'])}<br>"
            f"<small>{html.escape(j.get('israel_link', ''))} · posted {html.escape(str(j.get('posted') or '?')[:10])}</small> "
            f"· <a href='{html.escape(j['url'] or '')}'>Open posting</a></li>")


def main():
    t0 = time.time()
    companies = json.loads((ROOT / "companies.json").read_text())
    boards = json.loads((ROOT / "boards.json").read_text())
    only = sys.argv[1:]  # optional: names to test
    if only:
        companies = [c for c in companies if any(o.lower() in c["name"].lower() for o in only)]
        boards = [b for b in boards if any(o.lower() in b["name"].lower() for o in only)]

    all_jobs, errors, scanned = [], {}, 0
    with cf.ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "24"))) as ex:
        futs = [ex.submit(scan_company, c) for c in companies] + [ex.submit(scan_board, b) for b in boards]
        for f in cf.as_completed(futs):
            name, jobs, err = f.result()
            scanned += 1
            all_jobs += jobs
            if err:
                errors[name] = err

    matches = evaluate(all_jobs)
    seen_path = ROOT / "seen.json"
    first_run = not seen_path.exists()
    seen = set(json.loads(seen_path.read_text())) if not first_run else set()
    new = [m for m in matches if m["id"] not in seen]

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now}] scanned {scanned} sources, {len(all_jobs)} jobs, {len(matches)} remote-US HR matches, "
          f"{len(new)} new, {len(errors)} source errors, {time.time() - t0:.0f}s")

    # live list
    md = [f"# Open remote-US HR roles at Israeli-linked companies\n",
          f"_{len(matches)} matching roles open (list changes only when roles open or close; see the Actions tab for the last run time)_\n",
          "★★★ core HRBP/People/L&D · ★★ talent/recruiting/rewards · ★ senior stretch\n"]
    md += [line_md(m) for m in matches] or ["_No matches right now._"]
    (ROOT / "current_matches.md").write_text("\n".join(md) + "\n")
    (ROOT / "last_run.json").write_text(json.dumps({"at": now, "sources": scanned, "jobs": len(all_jobs),
                                                    "matches": len(matches), "new": len(new),
                                                    "errors": errors}, indent=1, ensure_ascii=False))
    if only:
        for m in matches:
            print(" ", stars(m["score"]), m["title"], "|", m["company"], "|", m["location"], "|", m["url"])
        print("errors:", json.dumps(errors, indent=1))
        return

    if new and os.getenv("DRY_RUN") != "1":
        if first_run or len(new) > 8:
            subj = f"HR Job Radar: {len(new)} open remote-US roles" + (" (initial scan)" if first_run else "")
            push_ntfy(subj, "\n".join(f"{m['title']} — {m['company']}" for m in new[:12]),
                      click=f"https://github.com/{os.getenv('GITHUB_REPOSITORY', '')}/blob/main/current_matches.md")
            gh_issue(subj, "\n".join(line_md(m) for m in new), ["digest"])
            email(subj, "<ul>" + "".join(row_html(m) for m in new) + "</ul>")
        else:
            for m in new:
                t = f"{stars(m['score'])} {m['title']} — {m['company']}"
                push_ntfy(t, f"{m['where']} · {m.get('israel_link', '')}\nTap to open the posting.", click=m["url"])
                gh_issue(t, line_md(m) + f"\n\nWhy: {m['why']}\nLocation text: `{m['location']}`",
                         ["new-role", f"score-{m['score']}"])
            email(f"New HR role{'s' if len(new) > 1 else ''}: " + ", ".join(m["company"] for m in new),
                  "<ul>" + "".join(row_html(m) for m in new) + "</ul>")

    seen |= {m["id"] for m in matches}
    seen_path.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
