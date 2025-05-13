# Gmail AI Digest

**Gmail AI Digest** is a small Python project that automatically fetches unread emails from your Gmail inbox, summarizes them using LangChain and OpenAI, and sends a daily digest email—all via the Gmail API.

---

## Features

* Reads unread Gmail messages from the last X days
* Summarizes them using a simple LangChain chain (OpenAI + prompt)
* Sends the summary to your inbox using the Gmail API (consider using proper SMTP setup instead)
* Marks processed messages as "read" so they’re skipped next time

---

## Setup

1. Enable the Gmail API in your Google Cloud project and create OAuth credentials for a Desktop app
2. Download `credentials.json` to the project directory
3. Create a `.env` file with:
   ```
   OPENAI_API_KEY=your_openai_key
   TARGET_EMAIL=your@email.com
   ```
---