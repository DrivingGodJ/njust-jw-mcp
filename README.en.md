[简体中文](README.md) | **English**

# NJUST JW MCP

An MCP server for the academic system at Nanjing University of Science and Technology (NJUST). Timetables, grades, exams, student records, degree plans and free classrooms — all reachable with one sentence from any MCP-capable AI client, with no browser and no repeated logins.

## What you can ask

| You say | What happens |
| --- | --- |
| "What classes do I have this week?" | Fetches the current timetable, grouped by weekday with periods, rooms and teachers |
| "Is my Monday afternoon class still on?" | Filters by week number and returns just that course |
| "How many credits is this semester?" | Reads the degree plan and returns a per-semester course list with credit subtotals |
| "What did I get in calculus?" | Queries grades, filterable by semester or course name |
| "Which classrooms are free Wednesday afternoon?" | Scans room occupancy and returns the free ones in that slot |
| "Where is my final exam?" | Returns exam time, room and seat number |

A timetable result, trimmed:

```text
Wednesday:
  01-05  Data Structures  [week 13]  Building A, Room 302
Monday:
  04-05  Linear Algebra  [weeks 13,14,15,16]  Building B, Room 108
  06-10  Data Structures  [week 14]  Building A, Room 302
```

A degree-plan result:

```json
{
  "totalCourses": 52,
  "totalCredits": 168,
  "terms": [
    { "semester": "2027-2028-1", "count": 12, "credits": 24 },
    { "semester": "2027-2028-2", "count": 11, "credits": 22.5 }
  ],
  "emptyTerms": ["2027-2028-3"]
}
```

## No browser, no password prompts

The university's single sign-on hands out a fresh salt on every login and expects the password to be encrypted client-side. This project does the same thing on the command line:

1. Read `pwdEncryptSalt` from the login page and encrypt `64 random characters + password` locally with `AES-128-CBC`;
2. Trade it for a CAS `CASTGC` ticket (30-day remember-me);
3. Exchange that for an academic-system session through `indexsso.jsp`.

All plain HTTP, roughly 3–4 seconds, no CAPTCHA. When the session expires the tools rebuild it automatically — the only symptom is one slower call.

## Install

**Requirements**: Node.js 18+, `python3` (bundled with macOS), `openssl` (system).

```bash
git clone https://github.com/DrivingGodJ/njust-jw-mcp.git
cd njust-jw-mcp
```

Create a credential file (local only, mode 600):

```bash
mkdir -p ~/.config/njust
cat > ~/.config/njust/credential.json <<'EOF'
{ "studentNo": "your student number", "password": "your SSO password" }
EOF
chmod 600 ~/.config/njust/credential.json
```

Register the server in your client (`claude_desktop_config.json` for Claude Desktop, `~/.workbuddy/mcp.json` for WorkBuddy — same `mcpServers` shape):

```json
{
  "mcpServers": {
    "njust-jw": {
      "command": "node",
      "args": ["/absolute/path/to/njust-jw-mcp/index.mjs"]
    }
  }
}
```

Restart the client and ask "what classes do I have this week" to verify.

**Optional environment variables**: `NJUST_JW_SCRIPTS` (override script directory), `NJUST_PYTHON` (choose a Python interpreter).

## Tools

| Tool | Purpose |
| --- | --- |
| `jw_schedule` | Current-semester timetable, filter by week, text or JSON |
| `jw_grades` | Course grades, filterable by semester or course name |
| `jw_exams` | Exam schedule: session, time, room, seat |
| `jw_profile` | Student record card (ID and exam numbers masked) |
| `jw_plan` | Degree plan with per-semester courses and credits |
| `jw_classrooms` | Room occupancy, or free rooms for a given day and slot |
| `jw_modules` | Available menu entries, useful before adding a new feature |
| `jw_page` | Generic in-system page fetch: title, tables, links, text |
| `jw_refresh_login` | Rebuild the session (falls back to a browser only if needed) |

## Boundaries

- **Unofficial.** It uses pages and endpoints the academic system exposes to students. A school-side upgrade may break it; fixes follow whatever changed.
- **Runs locally.** No data leaves your machine. The server only fetches and structures it.
- **Credentials are plaintext.** `credential.json` is mode 600 but unencrypted. If that bothers you, put it in the Keychain or an encrypted volume behind a wrapper script.
- **Your account only.** Sessions are per-student; sharing means sharing the same session.
- ID numbers and exam numbers are masked in `jw_profile`, which reduces accidental exposure but does not change where the raw data lives.

## Explore further

| Topic | Documentation |
| --- | --- |
| Pitfalls: CAS encryption details, Qiangzhi form quirks, incomplete semester dropdowns | [Technical notes](docs/NOTES.md) |
| Feedback | [Issues](https://github.com/DrivingGodJ/njust-jw-mcp/issues) |

MIT License. Endpoint details shift with each school-side release — issues describing your own institution's differences are welcome.
