import os
import base64
import requests
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv

from langchain_openai import OpenAIEmbeddings  
from langchain_community.vectorstores import FAISS 
from langchain.docstore.document import Document 

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


ORG     = os.getenv("AZURE_DEVOPS_ORG")
PROJECT = os.getenv("AZURE_DEVOPS_PROJECT")
REPO    = os.getenv("AZURE_DEVOPS_REPO")
PAT     = os.getenv("AZURE_DEVOPS_PAT")


if not all([ORG, PROJECT, REPO, PAT]):
    logging.error("Missing required Azure DevOps environment variables. Exiting.")
    exit(1)
if not os.getenv("OPENAI_API_KEY"):
    logging.error("Missing OPENAI_API_KEY environment variable. Exiting.")
    exit(1)


try:
    auth = base64.b64encode(f":{PAT}".encode()).decode()
    HEADERS = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    AZURE_DEVOPS_BASE_URL = (
        f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/git/repositories/{REPO}"
    )
except Exception as e:
    logging.error(f"Failed during initialization: {e}")
    exit(1)


def make_azure_devops_request(url: str) -> Dict[str, Any] | None:
    """Makes a GET request to Azure DevOps and handles errors."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Azure DevOps API request failed for URL {url}: {e}")
        return None


def fetch_closed_prs() -> List[Dict[str, Any]]:
    """Fetches completed pull requests from Azure DevOps."""
    logging.info("Fetching closed pull requests...")
    url = (
        f"{AZURE_DEVOPS_BASE_URL}/pullrequests?searchCriteria.status=completed&api-version=7.1-preview.1"
    )
    data = make_azure_devops_request(url)

    if data and "value" in data:
        logging.info(f"Fetched {len(data['value'])} closed PRs.")
        return data["value"]
    else:
        logging.warning("No closed PRs found or API error.")
        return []


def fetch_pr_diff_content(pr_id: int) -> str:
    """
    Fetches diff content for a given PR.
    Provides a summary of file changes.
    """
    logging.info(f"Fetching diff content for PR ID: {pr_id}...")
    
    # find latest diff
    iterations_url = (
        f"{AZURE_DEVOPS_BASE_URL}/pullrequests/{pr_id}/iterations?api-version=7.1-preview.1"
    )
    iterations_data = make_azure_devops_request(iterations_url)

    if not iterations_data or not iterations_data.get("value"):
        logging.warning(f"Could not get iterations for PR {pr_id}.")
        return f"Could not retrieve diff details for PR {pr_id}."

    latest_iter = iterations_data["value"][-1]["id"]
    changes_url = (
        f"{AZURE_DEVOPS_BASE_URL}/pullrequests/{pr_id}/iterations/{latest_iter}/changes?api-version=7.1-preview.1"
    )
    changes_data = make_azure_devops_request(changes_url)

    if not changes_data or "changeEntries" not in changes_data:
        logging.warning(f"Could not retrieve change entries for PR {pr_id}, iteration {latest_iter}.")
        # listing file paths
        diff_meta_url = (
            f"{AZURE_DEVOPS_BASE_URL}/pullrequests/{pr_id}/diffs?api-version=7.1-preview.1"
        )
        diff_meta = make_azure_devops_request(diff_meta_url)
        if diff_meta and "changes" in diff_meta:
            files = [c.get("item", {}).get("path", "unknown path") for c in diff_meta["changes"]]
            return f"Files changed in PR {pr_id}: {', '.join(files)}"
        return f"Could not retrieve any diff details for PR {pr_id}."

    # summary of file changes
    diff_summary_parts = []
    for change in changes_data.get("changeEntries", []):
        path = change.get("item", {}).get("path", "Unknown path")
        ctype = change.get("changeType", "edit").capitalize()
        diff_summary_parts.append(f"- {ctype} in file: {path}")

    if not diff_summary_parts:
        return f"No specific file changes identified in PR {pr_id}."

    # limit length
    max_files = 15
    if len(diff_summary_parts) > max_files:
        truncated = diff_summary_parts[:max_files]
        truncated.append(f"... and {len(diff_summary_parts) - max_files} more changes.")
        diff_summary_parts = truncated

    diff_text = "\n".join(diff_summary_parts)
    logging.info(f"Generated diff summary for PR {pr_id}.")
    return diff_text


def build_expertise_index() -> None:
    """Builds and saves a FAISS expertise index from closed PRs."""
    prs = fetch_closed_prs()
    if not prs:
        logging.error("No PRs to index. Exiting.")
        return

    docs: List[Document] = []
    for pr in prs:
        pr_id = pr.get("pullRequestId")
        title = pr.get("title", "Untitled PR")
        text = fetch_pr_diff_content(pr_id)
        reviewers = [r.get("uniqueName") for r in pr.get("reviewers", []) if r.get("uniqueName")]
        for reviewer in reviewers:
            content = f"Title: {title}\n{ text }"
            metadata = {"reviewer": reviewer, "pr_id": pr_id}
            docs.append(Document(page_content=content, metadata=metadata))

    if not docs:
        logging.error("No documents created for indexing. Exiting.")
        return

    logging.info(f"Indexing {len(docs)} documents...")
    embeddings = OpenAIEmbeddings()
    index = FAISS.from_documents(docs, embeddings)
    index.save_local("expertise_index")
    logging.info("Expertise FAISS index saved to 'expertise_index/'.")


if __name__ == "__main__":
    build_expertise_index()
    logging.info("Expertise index setup complete.")
