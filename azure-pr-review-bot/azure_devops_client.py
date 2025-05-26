import requests
import logging
import base64
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class AzureDevOpsClient:
    """
    Client for interacting with the Azure DevOps REST API.
    Handles API requests for pull requests, comments, reviewers, and file content.
    """
    def __init__(self, project_url: str, repo_name: str, pat: str):
        """
        Initializes the Azure DevOps Client.

        Args:
            project_url (str): The base URL for the Azure DevOps project (e.g., "https://dev.azure.com/ORG/PROJECT").
            repo_name (str): The name of the repository.
            pat (str): Personal Access Token for Azure DevOps API authentication.
        """
        if not all([project_url, repo_name, pat]):
            raise ValueError("Azure DevOps project_url, repo_name, and PAT must be provided.")

        self.base_api_url = f"{project_url}/_apis/git/repositories/{repo_name}"
        self.pat = pat
        self.headers = {
            "Authorization": f"Basic {base64.b64encode(f':{self.pat}'.encode()).decode()}",
            "Content-Type": "application/json",
        }
        logger.info(f"AzureDevOpsClient initialized for repo: {repo_name} at {project_url}")

    def _make_request(self, method: str, url: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None, timeout: int = 30) -> Optional[Dict[str, Any]]:
        """Makes an HTTP request to the Azure DevOps API and handles responses."""
        try:
            response = requests.request(method, url, headers=self.headers, params=params, json=json_data, timeout=timeout)
            response.raise_for_status()  # Raises HTTPError for bad responses (4XX or 5XX)
            if response.content:
                # Handle cases where response might be empty but successful (e.g., 204 No Content)
                if response.status_code == 204:
                    return {"status": "success", "message": "Operation successful with no content."}
                try:
                    return response.json()
                except ValueError: # Handles non-JSON responses if any
                    logger.error(f"Failed to decode JSON from response for URL {url}. Response text: {response.text[:200]}")
                    return None
            return None
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} for URL {url}. Response: {http_err.response.text[:500]}")
        except requests.exceptions.RequestException as req_err:
            logger.error(f"Request exception occurred: {req_err} for URL {url}")
        except Exception as e:
            logger.error(f"An unexpected error occurred during API request to {url}: {e}")
        return None

    def get_active_pull_requests(self, top: int = 50, status: str = "active") -> List[Dict[str, Any]]:
        """Fetches active pull requests."""
        url = f"{self.base_api_url}/pullrequests"
        params = {
            "searchCriteria.status": status,
            "api-version": "7.1-preview.1",
            "$top": top
        }
        logger.info(f"Fetching active PRs from {url} with params {params}")
        response_data = self._make_request("GET", url, params=params)
        return response_data.get("value", []) if response_data else []

    def get_pull_request_iterations(self, pr_id: int) -> List[Dict[str, Any]]:
        """Fetches iterations for a given pull request."""
        url = f"{self.base_api_url}/pullrequests/{pr_id}/iterations"
        params = {"api-version": "7.1-preview.1"}
        logger.info(f"Fetching iterations for PR ID: {pr_id}")
        response_data = self._make_request("GET", url, params=params)
        return response_data.get("value", []) if response_data else []

    def get_iteration_changes(self, pr_id: int, iteration_id: int) -> Optional[Dict[str, Any]]:
        """Fetches changes for a specific pull request iteration."""
        url = f"{self.base_api_url}/pullrequests/{pr_id}/iterations/{iteration_id}/changes"
        params = {"api-version": "7.1-preview.1"}
        logger.info(f"Fetching changes for PR ID: {pr_id}, Iteration ID: {iteration_id}")
        return self._make_request("GET", url, params=params)

    def get_file_content(self, file_path: str, commit_id: Optional[str] = None) -> Optional[str]:
        """
        Fetches the content of a specific file from the repository.
        If commit_id is provided, fetches the version from that commit. Otherwise, fetches from the default branch.
        """
        url = f"{self.base_api_url}/items"
        params = {
            "path": file_path.lstrip('/'), # Path should not start with / for this API
            "api-version": "7.1-preview.1",
            "$format": "text" # Request plain text content
        }
        if commit_id:
            params["versionDescriptor.version"] = commit_id
            params["versionDescriptor.versionType"] = "commit"

        logger.info(f"Fetching file content for path: {file_path} with params {params}")
        # This request type is different as it directly returns text, not JSON
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error fetching file {file_path}: {http_err}. Response: {http_err.response.text[:200]}")
        except requests.exceptions.RequestException as req_err:
            logger.error(f"Request exception fetching file {file_path}: {req_err}")
        return None


    def create_pr_thread_comment(self, pr_id: int, comment_content: str, thread_status: int = 1) -> Optional[Dict[str, Any]]:
        """
        Creates a new comment thread on a pull request.

        Args:
            pr_id (int): The ID of the pull request.
            comment_content (str): The content of the comment.
            thread_status (int): Status of the thread (e.g., 1 for Active, 2 for Fixed).
        """
        url = f"{self.base_api_url}/pullrequests/{pr_id}/threads"
        params = {"api-version": "7.1-preview.1"}
        payload = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": comment_content,
                    "commentType": 1  # 1 for text
                }
            ],
            "status": thread_status, # 1 = Active, 2 = Fixed, 3 = WontFix, 4 = Closed, 5 = ByDesign
            # Example property:
            # "properties": {
            #     "Microsoft.TeamFoundation.Discussion.SupportsMarkdown": {
            #         "type": "System.Int32",
            #         "value": 1
            #     }
            # }
        }
        logger.info(f"Creating comment on PR ID: {pr_id}")
        return self._make_request("POST", url, params=params, json_data=payload)

    def update_pr_reviewers(self, pr_id: int, reviewer_ids: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Updates the reviewers for a pull request.

        Args:
            pr_id (int): The ID of the pull request.
            reviewer_ids (List[Dict[str, Any]]): List of reviewer objects, e.g.,
                                                 [{"id": "guid1", "isRequired": True}, {"id": "guid2", "isRequired": False}].
        """
        url = f"{self.base_api_url}/pullrequests/{pr_id}" # This updates the entire PR
        # For just reviewers, there's a dedicated endpoint: /pullrequests/{pullRequestId}/reviewers/{reviewerId} for individual adds
        # Or update the main PR doc with the new list of reviewers. The PATCH method is safer.
        # Let's use the PATCH method to update the reviewers on the PR object directly.
        
        pr_update_url = f"{self.base_api_url}/pullrequests/{pr_id}?api-version=7.1-preview.1"
        
        # Fetch current PR data to get existing reviewers if you want to merge,
        # but the API for PATCH on /pullrequests/{pr_id} with a "reviewers" array typically overwrites.
        # For safety and typical usage, we overwrite with the intended complete list.
        # However, it's often better to add individual reviewers if that's the intent to avoid race conditions or accidental removals.
        # The API `POST /pullrequests/{pullRequestId}/reviewers/{reviewerId}` is for adding one by one.
        # Let's use PATCH on the PR for simplicity assuming we manage the full list.

        payload = {"reviewers": reviewer_ids}
        logger.info(f"Updating reviewers for PR ID: {pr_id} with payload: {payload}")

        return self._make_request("PATCH", pr_update_url, json_data=payload)


    def get_pull_request_details(self, pr_id: int) -> Optional[Dict[str, Any]]:
        """Fetches details for a single pull request."""
        url = f"{self.base_api_url}/pullrequests/{pr_id}"
        params = {"api-version": "7.1-preview.1"}
        logger.info(f"Fetching details for PR ID: {pr_id}")
        return self._make_request("GET", url, params=params)