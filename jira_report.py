import requests
from requests.auth import HTTPBasicAuth
import boto3
import urllib3
import configparser
from collections import defaultdict
from botocore.exceptions import ClientError
import json

# Suppress warnings for verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        response = requests.get(url, auth=auth, verify=False, timeout=10)
        sprints = response.json().get("values", [])
        return (sprints[0]['id'], sprints[0]['name']) if sprints else (None, None)
    except Exception as e:
        print(f"Sprint Fetch Error: {e}")
        return None, None

def build_report_string(sprint_name, total_issues, grouped_issues, reporting_statuses, user_name):
    report = f"User: {user_name} | {sprint_name} ({total_issues} Issues)\n"
    report += "=" * 40 + "\n"
    for status in reporting_statuses:
        issues_in_status = grouped_issues.get(status, [])
        report += f"\nStatus: {status} ({len(issues_in_status)} Issues)\n"
        for issue in issues_in_status:
            report += f"{issue['key']} | {issue['title']}\n"
            report += f"URL: {issue['url']}\n"
            report += f"  Last Comment: {' '.join(issue.get('last_comment', ['No comment']))}\n"
    return report

def send_ses_email(region, sender, recipient, subject, body):
    if not sender or sender == "None":
        print(f"Skipping Email: No sender_email configured for {recipient}")
        return
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
        print(f"SES Error for {recipient}: {e}")

def send_teams_message(webhook_url, text, title="Jira Report"):
    # Adaptive Card format often works better with Power Automate 202 responses
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                    {"type": "TextBlock", "text": text, "wrap": True}
                ],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.0"
            }
        }]
    }
    try:

        response = requests.post(webhook_url, json=payload, verify=False, timeout=10)
        # Treat 200 and 202 as success
        if response.status_code in [200, 202]:
            print(f"Teams notification success ({response.status_code})")
        else:
            print(f"Teams error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Teams Request Failed: {e}")

def run_report_for_profile(profile_name, config_section):
    domain = config_section.get('jira_domain')
    email = config_section.get('jira_email')
    token = config_section.get('jira_api_token')
    board_id = config_section.get('board_id')
    project_key = config_section.get('project_key')
    region = config_section.get('aws_region')
    sender = config_section.get('sender_email')
    recipient = config_section.get('recipient_email')

    webhook = config_section.get('teams_webhook_url')
    reporting_statuses = ["TO DO", "IN-PROGRESS", "ON HOLD", "READY FOR REVIEW", "REVIEW COMPLETED", "DONE"]
    auth = HTTPBasicAuth(email, token)
    headers = {"Accept": "application/json"}

    # 1. Sprint
    sprint_id, sprint_name = get_active_sprint(domain, board_id, email, token)
    if not sprint_id:
        print(f"[{profile_name}] No active sprint.")
        return

    # 2. Issues (Corrected JQL and endpoint)
    # Wrap project_key and email in quotes for JQL safety
    jql = f'project = "{project_key}" AND sprint = {sprint_id} AND assignee = "{email}"'
    search_url = f"https://{domain}.atlassian.net/rest/api/3/search/jql" 
    params = {"jql": jql, "fields": "summary,status"}

    res = requests.get(search_url, headers=headers, auth=auth, params=params, verify=False)
    if res.status_code != 200:
        print(f"Jira Search Failed: {res.status_code} - {res.text}")
        return
        
    issues = res.json().get("issues", [])
    grouped_issues = defaultdict(list)

    for issue in issues:
        status_name = issue["fields"]["status"]["name"].upper()
        issue_data = {
            "key": issue["key"],
            "title": issue["fields"]["summary"],
            "url": f"https://{domain}.atlassian.net/browse/{issue['key']}"
        }
        
        # Comments
        c_url = f"https://{domain}.atlassian.net/rest/api/3/issue/{issue['key']}/comment"
        c_res = requests.get(c_url, headers=headers, auth=auth, verify=False)
        if c_res.status_code == 200:
            comments = c_res.json().get("comments", [])
            issue_data["last_comment"] = extract_adf_text(comments[-1]["body"]) if comments else ["No comments"]
        else:
            issue_data["last_comment"] = ["N/A"]

        grouped_issues[status_name].append(issue_data)

    # 3. Report & Send
    report = build_report_string(sprint_name, len(issues), grouped_issues, reporting_statuses, profile_name)
    print(report)
    
    send_ses_email(region, sender, recipient, f"Report: {profile_name} - {sprint_name}", report)
    send_teams_message(webhook, report, title=f"Jira Report: {profile_name}")

def main():
    config = configparser.ConfigParser(interpolation=None)
    config.read('credentials.ini')

    # Iterate through all sections except DEFAULT
    profiles = config.sections()
    if not profiles:
        print("No user profiles found in credentials.ini")
        return

    for profile in profiles:
        print(f"\n{'='*20}\nProcessing: {profile}\n{'='*20}")
        try:
            run_report_for_profile(profile, config[profile])
        except Exception as e:
            print(f"Error processing profile {profile}: {e}")

if __name__ == "__main__":
    main()
