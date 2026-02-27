import requests
from requests.auth import HTTPBasicAuth
import os
import re
import boto3
import urllib3
from collections import defaultdict
from botocore.exceptions import ClientError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION (Ensure these are in your Environment Variables) ---
DOMAIN = os.environ.get('JIRA_DOMAIN')
EMAIL = os.environ.get('JIRA_EMAIL')
TOKEN = os.environ.get('JIRA_API_TOKEN')
BOARD_ID = os.environ.get('BOARD_ID')
PROJECT_KEY = os.environ.get('PROJECT_KEY')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
SENDER = os.environ.get('SENDER_EMAIL')
RECIPIENT = os.environ.get('RECIPIENT_EMAIL')

MY_REPORTING_STATUSES = ["TO DO", "IN-PROGRESS", "ON HOLD", "READY FOR REVIEW", "REVIEW COMPLETED", "DONE"]

def extract_adf_text(node):
    text_parts = []
    skip_variants = ["next step", "nextstep"]
    if not node: return text_parts
    if node.get("type") == "text":
        text = node.get("text", "").lower().replace(" ", "")
        if any(variant in text for variant in skip_variants):
            return text_parts 
        text_parts.append(node.get("text"))
    if "content" in node:
        for child in node["content"]:
            text_parts.extend(extract_adf_text(child))
    return text_parts

def get_active_sprint(domain, board_id, email, api_token):
    auth = HTTPBasicAuth(email, api_token)
    url = f"https://{domain}.atlassian.net/rest/agile/1.0/board/{board_id}/sprint?state=active"
    try:
        response = requests.get(url, auth=auth, verify=False)
        sprints = response.json().get("values", [])
        return (sprints[0]['id'], sprints[0]['name']) if sprints else (None, None)
    except:
        return None, None

def build_report_string(sprint_name, total_issues, grouped_issues, reporting_statuses):
    """Builds the plain-text report matching your printed format for the email body."""
    report = f"{sprint_name} ({total_issues} Issues)\n"
    report += "=" * 40 + "\n"

    for status in reporting_statuses:
        issues_in_status = grouped_issues.get(status)
        if not issues_in_status:
            matched_key = next((k for k in grouped_issues if k.lower() == status.lower()), None)
            issues_in_status = grouped_issues.get(matched_key)

        if issues_in_status:
            report += f"\nStatus: {status} ({len(issues_in_status)} Issues)\n"
            for issue in issues_in_status:
                report += f"{issue['key']} | Issue Title: {issue['title']}\n"
                report += f"URL: {issue['url']}\n"
                
                comment_lines = issue.get('last_comment', [])
                if isinstance(comment_lines, list):
                    for line in comment_lines:
                        report += f"  {line.strip()}\n"
                else:
                    report += f"  {comment_lines}\n"
                report += "\n"
        else:
            report += f"\nStatus: {status} (0 Issues)\n"
    return report

def send_email(subject, body):
    client = boto3.client('ses', region_name=AWS_REGION)
    try:
        client.send_email(
            Source=SENDER,
            Destination={'ToAddresses': [RECIPIENT]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Text': {'Data': body}}
            }
        )
        print("Email sent successfully!")
    except ClientError as e:
        print(f"SES Error: {e.response['Error']['Message']}")

def main():
    auth = HTTPBasicAuth(EMAIL, TOKEN)
    headers = {"Accept": "application/json"}
    
    # 1. Get Active Sprint
    sprint_id, sprint_name = get_active_sprint(DOMAIN, BOARD_ID, EMAIL, TOKEN)
    if not sprint_id:
        print("No active sprint found.")
        return

    # 2. Fetch Issues
    jql = f'project = {PROJECT_KEY} AND sprint = {sprint_id} AND assignee = currentUser()'
    search_url = f"https://{DOMAIN}.atlassian.net/rest/api/3/search/jql" 
    params = {"jql": jql, "fields": "summary,status"}

    response = requests.get(search_url, headers=headers, auth=auth, params=params, verify=False)
    print(response.text)
    issues = response.json().get("issues", [])
    
    grouped_issues = defaultdict(list)
    total_issues = 0

    # 3. Process Issues and fetch last comments
    for issue in issues:
        total_issues += 1
        status_name = issue["fields"]["status"]["name"]
        
        issue_data = {
            "key": issue["key"],
            "title": issue["fields"]["summary"],
            "url": f"https://{DOMAIN}.atlassian.net/browse/{issue['key']}"
        }
        
        # Fetch individual comments for ADF parsing
        c_url = f"https://{DOMAIN}.atlassian.net/rest/api/3/issue/{issue['key']}/comment"
        c_res = requests.get(c_url, headers=headers, auth=auth, verify=False)
        
        if c_res.status_code == 200:
            comments = c_res.json().get("comments", [])
            if comments:
                issue_data["last_comment"] = extract_adf_text(comments[-1]["body"])
            else:
                issue_data["last_comment"] = ["No comments"]
        else:
            issue_data["last_comment"] = ["Failed to fetch comments"]

        grouped_issues[status_name].append(issue_data)

    # 4. Generate the report string
    final_report = build_report_string(sprint_name, total_issues, grouped_issues, MY_REPORTING_STATUSES)
    
    # 5. Print to console (formatted) and send email
    print(final_report)
    send_email(f"Weekly Jira Report: {sprint_name}", final_report)

if __name__ == "__main__":
    main()
