import os
import sys
import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("Jira Assets Server")

# Global variables for Jira configuration
JIRA_DOMAIN = os.environ.get("JIRA_DOMAIN")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN")
JIRA_WORKSPACE_ID = os.environ.get("JIRA_WORKSPACE_ID")

def get_auth():
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        raise ValueError("JIRA_EMAIL and JIRA_API_TOKEN environment variables are required.")
    return (JIRA_EMAIL, JIRA_API_TOKEN)

def get_workspace_id() -> str:
    global JIRA_WORKSPACE_ID
    if JIRA_WORKSPACE_ID:
        return JIRA_WORKSPACE_ID
    
    if not JIRA_DOMAIN:
        raise ValueError("JIRA_DOMAIN environment variable is required if JIRA_WORKSPACE_ID is not provided.")
    
    url = f"https://{JIRA_DOMAIN}/rest/servicedeskapi/assets/workspace"
    response = httpx.get(url, auth=get_auth(), headers={"Accept": "application/json"})
    response.raise_for_status()
    
    data = response.json()
    JIRA_WORKSPACE_ID = data.get("workspaceId")
    if not JIRA_WORKSPACE_ID:
        raise ValueError("Could not discover workspaceId from Jira.")
    
    return JIRA_WORKSPACE_ID

def get_base_url() -> str:
    if not JIRA_DOMAIN:
        raise ValueError("JIRA_DOMAIN environment variable is required.")
    workspace_id = get_workspace_id()
    # Using the standard Assets REST API base path
    return f"https://{JIRA_DOMAIN}/jsm/assets/workspace/{workspace_id}/v1"

@mcp.tool()
def execute_aql(query: str, start_at: int = 0, max_results: int = 25, include_attributes: bool = True) -> dict:
    """
    Executes an AQL (Asset Query Language) query to search for objects in Jira Service Management Assets.
    
    Args:
        query: The AQL query string.
        start_at: The starting index for pagination (default: 0).
        max_results: The maximum number of results to return (default: 25).
        include_attributes: Whether to include object attributes in the response (default: True).
    """
    base_url = get_base_url()
    url = f"{base_url}/object/aql"
    
    payload = {
        "qlQuery": query
    }
    
    params = {
        "startAt": start_at,
        "maxResults": max_results,
        "includeAttributes": str(include_attributes).lower()
    }
    
    response = httpx.post(url, auth=get_auth(), params=params, json=payload, headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()

@mcp.tool()
def get_object(object_id: str) -> dict:
    """
    Retrieves the details of a single Jira Service Management Asset object by its unique ID.
    
    Args:
        object_id: The unique identifier of the asset object.
    """
    base_url = get_base_url()
    url = f"{base_url}/object/{object_id}"
    
    response = httpx.get(url, auth=get_auth(), headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()

@mcp.tool()
def update_object(object_id: str, object_type_id: str, attributes: list) -> dict:
    """
    Updates an existing object in Jira Service Management Assets.
    
    Args:
        object_id: The ID of the object to update.
        object_type_id: The ID of the object type.
        attributes: An array of attribute objects to update. Each element must contain 'objectTypeAttributeId' and 'objectAttributeValues' (an array with a 'value' key). Example: [{"objectTypeAttributeId": "265", "objectAttributeValues": [{"value": "A new value"}]}]
    """
    base_url = get_base_url()
    url = f"{base_url}/object/{object_id}"
    
    payload = {
        "objectTypeId": object_type_id,
        "attributes": attributes
    }
    
    response = httpx.put(url, auth=get_auth(), json=payload, headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    mcp.run()
