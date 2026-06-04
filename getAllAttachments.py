#!/usr/bin/env python3
"""
Download all attachments from a Confluence Cloud page.

USAGE:
  getAllAttachments.py --email EMAIL --token TOKEN [--site SITE] [--page-id PAGE_ID] [--output OUTPUT]

ARGUMENTS:
  --email     Atlassian account email address
  --token     Atlassian API token
                Generate one at: https://id.atlassian.com/manage-profile/security/api-tokens
                Steps: Log in -> Security -> API tokens -> Create API token -> Copy the value
  --site      Confluence site URL (default: https://txone.atlassian.net)
  --page-id   Confluence page ID (numeric ID from the page URL)
  --output    Output directory for downloaded attachments (default: ./attachments)

SETUP (first time only):
  python3 -m venv venv
  source venv/bin/activate        # macOS/Linux
  venv/Scripts/activate           # Windows
  pip install requests

EXAMPLE:
  python getAllAttachments.py \\
    --email user@company.com \\
    --token "ATATT3x..." \\
    --site "https://mycompany.atlassian.net" \\
    --page-id 123456789 \\
    --output "./downloads"

NOTE:
  Confluence Cloud routes binary attachment downloads through api.media.atlassian.com.
  This script uses the api.atlassian.com proxy (requires the cloud site ID) so that
  API token authentication is accepted — direct site download URLs only accept OAuth.
"""

import argparse
import re
import sys
import requests
from pathlib import Path


def get_cloud_id(email: str, token: str, site: str) -> str:
    """Extract cloud ID from any attachment ARI via the REST API."""
    auth = (email, token)
    url = f"{site}/wiki/rest/api/content"
    r = requests.get(url, auth=auth, params={"limit": 1, "expand": "space"})
    r.raise_for_status()

    # Fetch one attachment to get its ARI which contains the cloud ID
    spaces_url = f"{site}/wiki/rest/api/space"
    r = requests.get(spaces_url, auth=auth, params={"limit": 1})
    r.raise_for_status()

    # Faster: pull cloud ID from accessible-resources via the site itself
    r = requests.get(f"{site}/wiki/rest/api/content/search", auth=auth,
                     params={"cql": "type=page", "limit": 1, "expand": "space.description"})
    r.raise_for_status()

    # Get cloud ID from the first attachment ARI on any content
    # Use the page attachments endpoint which includes ARI
    return None  # fallback: discovered dynamically per attachment


def discover_cloud_id(email: str, token: str, site: str, page_id: str) -> str:
    """Get the Atlassian cloud ID from the page's attachment ARI."""
    auth = (email, token)
    url = f"{site}/wiki/rest/api/content/{page_id}?expand=children.attachment"
    r = requests.get(url, auth=auth)
    r.raise_for_status()
    data = r.json()

    results = data.get("children", {}).get("attachment", {}).get("results", [])
    if not results:
        # Try fetching attachments directly
        att_url = f"{site}/wiki/rest/api/content/{page_id}/child/attachment?limit=1"
        r2 = requests.get(att_url, auth=auth)
        r2.raise_for_status()
        results = r2.json().get("results", [])

    if not results:
        raise RuntimeError("No attachments found — cannot determine cloud ID.")

    ari = results[0].get("ari", "")
    # ARI format: ari:cloud:confluence:{cloud_id}:attachment/{id}
    match = re.search(r"ari:cloud:confluence:([a-f0-9-]+):", ari)
    if not match:
        raise RuntimeError(f"Could not extract cloud ID from ARI: {ari}")
    return match.group(1)


def get_attachments(email: str, token: str, site: str, page_id: str) -> list[dict]:
    url = f"{site}/wiki/rest/api/content/{page_id}/child/attachment"
    params = {"limit": 100, "start": 0}
    auth = (email, token)
    attachments = []

    while True:
        response = requests.get(url, params=params, auth=auth)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        attachments.extend(results)
        if len(attachments) >= data["size"] or not results:
            break
        params["start"] += len(results)

    return attachments


def download_attachment(cloud_id: str, auth: tuple, attachment: dict, output_dir: Path) -> None:
    title = attachment["title"]
    download_path = attachment["_links"]["download"]
    # Route via api.atlassian.com proxy — this accepts API token Basic Auth,
    # unlike the direct site download URL which requires OAuth/SSO session.
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki{download_path}"

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / title

    response = requests.get(url, auth=auth, stream=True, allow_redirects=True)
    response.raise_for_status()

    with open(dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = dest.stat().st_size / 1024
    print(f"  Downloaded: {title} ({size_kb:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(
        description="Download all attachments from a Confluence Cloud page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s --email user@company.com --token "ATATT3x..." --page-id 123456789
  %(prog)s --email user@company.com --token "ATATT3x..." --site https://mycompany.atlassian.net --page-id 123456789 --output ./downloads

Generate an API token at: https://id.atlassian.com/manage-profile/security/api-tokens
        """,
    )
    parser.add_argument("--email", required=True, help="Atlassian account email")
    parser.add_argument("--token", required=True, help="Atlassian API token")
    parser.add_argument("--site", default="https://txone.atlassian.net", help="Confluence site URL (default: https://txone.atlassian.net)")
    parser.add_argument("--page-id", required=True, help="Confluence page ID")
    parser.add_argument("--output", default="./attachments", help="Output directory (default: ./attachments)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    auth = (args.email, args.token)

    print(f"Fetching attachments from page {args.page_id}...")
    try:
        attachments = get_attachments(args.email, args.token, args.site, args.page_id)
    except requests.HTTPError as e:
        print(f"Error fetching attachments: {e}", file=sys.stderr)
        sys.exit(1)

    if not attachments:
        print("No attachments found.")
        return

    print(f"Resolving cloud ID...")
    try:
        cloud_id = discover_cloud_id(args.email, args.token, args.site, args.page_id)
    except Exception as e:
        print(f"Error resolving cloud ID: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(attachments)} attachment(s). Downloading to: {output_dir}")
    errors = []
    for attachment in attachments:
        try:
            download_attachment(cloud_id, auth, attachment, output_dir)
        except requests.HTTPError as e:
            title = attachment.get("title", "unknown")
            print(f"  FAILED: {title} — {e}", file=sys.stderr)
            errors.append(title)

    print(f"\nDone. {len(attachments) - len(errors)}/{len(attachments)} file(s) downloaded.")
    if errors:
        print(f"Failed: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
