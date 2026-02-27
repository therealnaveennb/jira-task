import requests
from requests.auth import HTTPBasicAuth
import os
import boto3
from collections import defaultdict
from botocore.exceptions import ClientError

# --- JIRA LOGIC ---

def get_active_sprint(domain, board_id, email, api_token):
    auth = HTTPBasicAuth(email, api_token)
    url = f"https://{domain}.atlassian.net/rest/agile/1.0/board/{board_id}/sprint?state=active"
    response = requests.get(url, auth=auth)
    sprints = response.json().get("values", [])
    return (sprints[0]['id'], sprints[0]['name']) if sprints else (None, None)

def extract_adf_text(node):
    text_parts = []
    if not node: return text_parts
    if node.get("type") == "text":
        text_parts.append(node.get("text", ""))
    if "content" in node:
        for child in node["content"]:
            text_parts.extend(extract_adf_text(child))
    return text_parts

def generate_report():
    # Load settings from GitHub Env
    domain = os.environ['JIRA_DOMAIN']
    email = os.environ['JIRA_EMAIL']
    token = os.environ['JIRA_API_TOKEN']
    board_id = os.environ['BOARD_ID']
    project_key = os.environ['PROJECT_KEY']
    
    s_id, s_name = get_active_sprint(domain, board_id, email, token)
    if not s_id:
        print("No active sprint found.")
        return

    # Fetch Issues
    auth = HTTPBasicAuth(email, token)
    jql = f'project = "{project_key}" AND sprint = {s_id}'
    res = requests.get(f"https://{domain}.atlassian.net/rest/api/3/search", auth=auth, params={"jql": jql})
    issues = res.json().get("issues", [])

    report = f"Jira Weekly Report: {s_name}\n" + "="*30 + "\n"
    for issue in issues:
        key = issue["key"]
        title = issue["fields"]["summary"]
        status = issue["fields"]["status"]["name"]
        report += f"[{status}] {key}: {title}\n"

    # Send via SES
    ses = boto3.client('ses', region_name=os.environ['AWS_REGION'])
    ses.send_email(
        Source=os.environ['SENDER_EMAIL'],
        Destination={'ToAddresses': [os.environ['RECIPIENT_EMAIL']]},
        Message={
            'Subject': {'Data': f"Weekly Report: {s_name}"},
            'Body': {'Text': {'Data': report}}
        }
    )
    print("Email sent!")

if __name__ == "__main__":
    generate_report()
