import json
import os
from jinja2 import Template

# Load data from environment
user_data = json.loads(os.environ.get('USER_DATA_JSON', '[]'))
webhook_url = os.environ.get('TEAMS_WEBHOOK_URL')

if webhook_url:
    print(f"Webhook URL detected. Length: {len(webhook_url)} characters.")
else:
    print("WARNING: TEAMS_WEBHOOK_URL is missing or empty!")

template_str = """[DEFAULT]
jira_domain = {{ jira_domain }}
board_id = {{ board_id }}
project_key = {{ project_key }}
aws_region = {{ aws_region }}
sender_email = {{ sender_email }}
teams_webhook_url = {{ teams_webhook_url }}

{% for user in users %}
[{{ user.name }}]
jira_email = {{ user.email }}
jira_api_token = {{ user.token }}
recipient_email = {{ user.recipient }}
{% endfor %}
"""

template = Template(template_str)
rendered_config = template.render(
    jira_domain="mitsogo",
    board_id="21",
    project_key="DevOps",
    aws_region="ap-south-1",
    sender_email=os.environ.get('SENDER_EMAIL'),
    users=user_data,
    teams_webhook_url=webhook_url
)

with open("credentials.ini", "w") as f:
    f.write(rendered_config)
