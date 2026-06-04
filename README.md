# PRD  ConfluenceGetAllAttachments

**Created:** 2026-06-04
**Updated:** 2026-06-04

---

## Overview

A single Python script that downloads all attachments (PPTX, DOCX, PDF, MP4, …)
from a Confluence Cloud page using an Atlassian API token.  
Designed for bulk-retrieving training materials hosted on Confluence without
manual browser downloads.

---

## Background

Manually downloading 10+ large files (including MP4s in the hundreds of MB) via
the Confluence web UI is tedious.  An API-driven script eliminates that friction.

---

## Script: `getAllAttachments.py`

### Usage

```
python getAllAttachments.py \
  --email  <atlassian-email> \
  --token  <atlassian-api-token> \
  [--site  <confluence-site-url>]   # default: https://xxxxx.atlassian.net
  [--page-id <page-id>]             # numeric ID from the page URL
  [--output  <output-directory>]    # default: ./attachments
```

### Arguments

| Argument    | Required | Description |
|-------------|----------|-------------|
| `--email`   | yes      | Atlassian account email address |
| `--token`   | yes      | Atlassian API token (see below) |
| `--site`    | no       | Confluence site URL (default: `https://xxxxx.atlassian.net`) |
| `--page-id` | yes      | Numeric page ID (visible in the page URL) |
| `--output`  | no       | Local destination directory (created if absent) |

### Generating an Atlassian API token

1. Log in to your Atlassian account at `https://id.atlassian.com`
2. Go to **Security** -> **API tokens**: `https://id.atlassian.com/manage-profile/security/api-tokens`
3. Click **Create API token**
4. Give it a descriptive label (e.g. `confluence-download-script`)
5. Copy the token value immediately.  It is only shown once
6. Pass it as `--token "ATATT3x..."`

> **Note:** API tokens are tied to your Atlassian account and inherit your Confluence
> permissions. If you cannot access a page in the browser, the script will also fail.

### Example

```bash
python getAllAttachments.py \
  --email  myemail@xxxxx.com \
  --token  "ATATT3x..." \
  --site   "https://xxxxx.atlassian.net" \
  --page-id 0123456789 \
  --output  "./output"
```

Output:

```
Fetching attachments from page 0123456789...
Resolving cloud ID...
Found 10 attachment(s). Downloading to: output
  Downloaded: some_file.pptx (3970.5 KB)
  Downloaded: another_file.pptx (1634.7 KB)
  Downloaded: yet_another_one.mp4 (31774.3 KB)
  ...
Done. 10/10 file(s) downloaded.
```

---

## Technical Design

### How it works

1. **List attachments**  `GET /wiki/rest/api/content/{pageId}/child/attachment`  
   Returns metadata for all attachments including `_links.download` and `ari`.

2. **Discover cloud ID**  extracted from the first attachment's ARI string:  
   `ari:cloud:confluence:{cloud_id}:attachment/{id}`  
   The cloud ID is required for the download proxy (step 3).

3. **Download via `api.atlassian.com` proxy**  each file is fetched from:  
   `https://api.atlassian.com/ex/confluence/{cloud_id}/wiki{download_path}`  
   This endpoint accepts Basic Auth (email + API token) and redirects to  
   `api.media.atlassian.com` with a short-lived signed JWT token for the  
   actual CDN download.

4. **Stream to disk** files are streamed in 8 KB chunks to handle large MP4s
   without loading them fully into memory.

### Dependencies

```
pip install requests
```

No other dependencies.

---

## Findings & Lessons Learned

### The Attachment Download 401 Problem

**Symptom:** The REST API calls for listing attachments worked fine (HTTP 200),
but every download attempt returned HTTP 401 with:
```
Www-Authenticate: OAuth realm="https%3A%2F%2Fxxxxx.atlassian.net%2Fwiki"
```

**Root cause:** `xxxxx.atlassian.net` has SAML SSO enforced.  Atlassian Cloud
routes binary attachment downloads through CloudFront CDN, and that CDN layer
**only accepts OAuth tokens** it explicitly rejects Basic Auth (email + API
token) even though Basic Auth works perfectly for all REST API calls.

This is an Atlassian Cloud architectural split:  
- **REST API (`/wiki/rest/api/...`)** → accepts Basic Auth with API token  
- **Binary download (`/wiki/download/attachments/...`)** → requires OAuth / browser session  

### The Fix: `api.atlassian.com` Proxy

The key discovery was that routing the download through the Atlassian API
gateway (`https://api.atlassian.com/ex/confluence/{cloudId}/wiki/...`) instead
of directly through the site URL **does** accept Basic Auth.  The gateway
performs auth, then issues a short-lived signed JWT and redirects to
`api.media.atlassian.com` for the actual file transfer.

**This is not documented anywhere obvious.**  It was found by inspecting the
`ari` field in the v2 API response (which contains the cloud ID) and then
experimenting with the `api.atlassian.com/ex/confluence/` proxy prefix.

### Bearer Token Dead End

Using `Authorization: Bearer <api-token>` on the direct site download URL
returned HTTP 302 → redirect to `/wiki/login.action?...` (the login page).
The API token is not a valid OAuth Bearer token.  It only works as the password
in HTTP Basic Auth for the REST API gateway.

### Things Tried That Did Not Work

| Approach | Result | Reason |
|---|---|---|
| Basic Auth on `/wiki/download/attachments/` | 401 | CDN requires OAuth |
| Explicit `Authorization: Basic ...` header | 401 | Same CDN layer |
| `X-Atlassian-Token: no-check` header | 401 | CSRF bypass, unrelated to auth |
| `Bearer <api-token>` on download URL | 302 → login page | Token not a valid OAuth Bearer |
| `GET /wiki/rest/api/content/{att-id}/download` | 404 | Endpoint does not exist |
| `GET /wiki/api/v2/attachments/{id}/download` | 404 | Endpoint does not exist |
| Cookie-based session via `/rest/auth/1/session` | 401 | API tokens rejected for session login on SSO orgs |

### Cloud ID Discovery

The cloud ID is embedded in every attachment's `ari` field:
```
ari:cloud:confluence:fa1bf9bc-bfa7-4eae-a2e9-4bca6ca2ea99:attachment/2546237524
```

This is available in both REST API v1 (`?expand=children.attachment`) and v2
(`/wiki/api/v2/pages/{id}/attachments`) responses.  No separate API call is
needed to discover it.

### Pagination

The v1 attachment list API paginates at 100 items per page using `?limit=100&start=N`.
The script handles this correctly with a `while` loop.

---

## Files

| File | Description |
|------|-------------|
| `getAllAttachments.py` | Main script |
| `ConfluenceGetAllAttachments.prd.md` | This file |

---

## Known Limitations

- **No recursive page traversal** only downloads attachments on the specified
  page, not on child pages.  Run with a different `--page-id` for each page.
- **No file-type filtering** downloads everything attached to the page.
  Add a `--types` argument if selective download is needed in the future.
- **Token scope** the Atlassian API token inherits the account's Confluence
  permissions.  If the page is restricted, downloads will fail with 403.
