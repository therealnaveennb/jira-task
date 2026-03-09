import requests
from requests.auth import HTTPBasicAuth
import os
import re
import boto3
import urllib3
import configparser
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

def run_report_for_profile(config_section):
    """Executes the reporting logic for a specific profile."""
    # Extract values from the config section
    domain = config_section.get('jira_domain')
    email = config_section.get('jira_email')
    token = config_section.get('jira_api_token')
    board_id = config_section.get('board_id')
    project_key = config_section.get('project_key')
    aws_region = config_section.get('aws_region')
    sender = config_section.get('sender_email')
    recipient = config_section.get('recipient_email')
    
    reporting_statuses = ["TO DO", "IN-PROGRESS", "ON HOLD", "READY FOR REVIEW", "REVIEW COMPLETED", "DONE"]
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}

    # 1. Get Active Sprint
    sprint_id, sprint_name = get_active_sprint(domain, board_id, email, token)
    if not sprint_id:
        print(f"[{email}] No active sprint found.")
        return

    # 2. Fetch Issues assigned to the current email
    jql = f'project = {project_key} AND sprint = {sprint_id} AND assignee = "{email}"'
    search_url = f"https://{domain}.atlassian.net/rest/api/3/search" 
    params = {"jql": jql, "fields": "summary,status"}

    res = requests.get(search_url, headers=headers, auth=auth, params=params, verify=False)
    issues = res.json().get("issues", [])
    
    grouped_issues = defaultdict(list)
    for issue in issues:
        status_name = issue["fields"]["status"]["name"]
        issue_data = {
            "key": issue["key"],
            "title": issue["fields"]["summary"],
            "url": f"https://{domain}.atlassian.net/browse/{issue['key']}"
        }
        
        # Fetch last comment
        c_url = f"https://{domain}.atlassian.net/rest/api/3/issue/{issue['key']}/comment"
        c_res = requests.get(c_url, headers=headers, auth=auth, verify=False)
        if c_res.status_code == 200:
            comments = c_res.json().get("comments", [])
            issue_data["last_comment"] = extract_adf_text(comments[-1]["body"]) if comments else ["No comments"]
        else:
            issue_data["last_comment"] = ["Failed to fetch comments"]

        grouped_issues[status_name].append(issue_data)

    # 3. Build & Send
    final_report = build_report_string(sprint_name, len(issues), grouped_issues, reporting_statuses)
    
    # We pass the AWS details to the existing send_email logic
    send_ses_email(aws_region, sender, recipient, f"Weekly Jira Report: {sprint_name}", final_report)

def send_ses_email(region, sender, recipient, subject, body):
    client = boto3.client('ses', region_name=region)
    try:
        client.send_email(
            Source=sender,
            Destination={'ToAddresses': [recipient]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Text': {'Data': body}}
            }
        )
        print(f"Email sent successfully to {recipient}")
    except ClientError as e:
        print(f"Error sending to {recipient}: {e}")

def main():
    config = configparser.ConfigParser()
    config.read('credentials.ini')

    # Iterate through all sections except DEFAULT
    for profile in config.sections():
        print(f"--- Processing Profile: {profile} ---")
        try:
            run_report_for_profile(config[profile])
        except Exception as e:
            print(f"Failed to process {profile}: {e}")

if __name__ == "__main__":
    main()
