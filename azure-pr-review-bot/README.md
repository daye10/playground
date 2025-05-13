# Azure PR Review Bot

A small Python project to explore LangChain and address issue of pull requests that sit unreviewed. This bot fetches active PRs, generates AI‑powered summaries, rotates a couple of “primary” reviewers weekly, and suggests an extra reviewer based on past expertise.

---

## Features

- **Azure DevOps**: Lists only active PRs created in the last 14 days  
- **OpenAI Summaries**: Condenses file‑change metadata into a brief summary  
- **Round‑Robin Rotation**: Cycles core reviewers each run  
- **FAISS‑backed RAG**: Recommends one optional reviewer per PR  
- **Teams Notifications**: Posts a digest into a Teams channel via Incoming Webhook  

---


### Install dependencies

```bash
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```ini
# Azure DevOps
AZURE_DEVOPS_ORG=your-org
AZURE_DEVOPS_PROJECT=your-project
AZURE_DEVOPS_REPO=your-repo
AZURE_DEVOPS_PAT=your-pat

# OpenAI
OPENAI_API_KEY=sk-...

# Reviewers (comma‑separated)
REVIEWERS_LIST=alice@company.com,bob@company.com,charles@company.com
NUM_PRIMARY_REVIEWERS=2
NUM_OPTIONAL_REVIEWERS=1

# Teams Incoming Webhook
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/…
```
