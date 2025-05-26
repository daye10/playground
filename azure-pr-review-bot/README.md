# Azure PR Review & Codebase Assistant

This project provides a suite of tools designed to enhance the code review process in Azure DevOps and facilitate interaction with your codebase. It features an automated PR Review Bot that provides AI-driven summaries and detailed feedback, and a Code Chatbot for querying your codebase.

---

## Core Components

1.  **PR Review Bot (`bot.py`):** The main application that monitors Azure DevOps for active pull requests, performs AI analysis, assigns reviewers, and posts feedback.
2.  **Codebase Indexer (script in `code_context_provider.py` or `setup_codebase_index.py`):** A utility script to scan your entire local codebase and create a searchable FAISS vector index. This index is used by both the PR Review Bot (for broader context) and the Code Chatbot.
3.  **Expertise Indexer (`setup_expertise_index.py`):** A script that processes past pull requests to build an expertise model, which is then used by the PR Review Bot to recommend optional reviewers.
4.  **Code Chatbot (`code_chatbot.py`):** An interactive command-line tool that allows you to "chat" with your codebase using the index created by the Codebase Indexer.

---

## Features

### 1. PR Review Bot (`bot.py`)

* **Azure DevOps Integration:**
    * Fetches active pull requests created within a configurable timeframe (e.g., last 14 days).
    * Retrieves PR details, iterations, and file changes.
* **AI-Powered PR Analysis:**
    * Generates a **brief AI summary** of PR changes based on the diff.
    * Performs a **detailed AI review** leveraging:
        * The overall PR summary.
        * A summary of file change operations (added, edited, deleted).
        * The full content of files actually changed in the PR (fetched live).
        * Relevant snippets from the broader project codebase (via RAG from the local codebase index) to assess coherence and integration.
    * Focuses review on modifications made *within the current PR*, avoiding critique of unrelated existing code.
* **Reviewer Assignment:**
    * Assigns primary reviewers using a round-robin system, managed via `rr_state.json`.
    * Recommends optional reviewers based on expertise inferred from past PRs (using the `expertise_index` and RAG).
    * Avoids assigning the PR author as a reviewer.
    * Allows mapping of email addresses to Azure DevOps User IDs for assignment.
* **Automated Commenting:**
    * Posts the brief summary and detailed AI observations as a comment on the Azure DevOps PR thread.
* **(Mentioned in original README) Teams Notifications:** Posts a digest into a Teams channel via Incoming Webhook. (Note: Python code for this feature was not explicitly provided in the `bot.py` script but is retained from the original README).

### 2. Codebase Indexer (e.g., `code_context_provider.py` script)

* Scans a specified local project directory.
* Filters files by relevant extensions and ignores specified directories.
* Splits code and text files into manageable chunks.
* Generates embeddings using OpenAI models.
* Builds and saves a FAISS vector store locally (default: `codebase_context_index/`).
* Highly configurable via command-line arguments.

### 3. Expertise Indexer (`setup_expertise_index.py`)

* Fetches completed pull requests from Azure DevOps.
* Extracts information about reviewers and summaries of changes for past PRs.
* Builds a FAISS vector index (default: `expertise_index/`) to capture reviewer expertise.
* This index is used by the PR Review Bot for recommending optional reviewers.

### 4. Code Chatbot (`code_chatbot.py`)

* Provides an interactive command-line interface to ask questions about your codebase.
* Utilizes the FAISS index created by the Codebase Indexer for context.
* Maintains conversational history for follow-up questions using modern LangChain memory management (`RunnableWithMessageHistory`).
* Supports both synchronous and asynchronous querying.
* Configurable via command-line arguments.

---

## Future Enhancements (Ideas)

* **VS Code Extension:** Integrate the Code Chatbot directly into VS Code.
* **Advanced RAG Strategies:** Explore more sophisticated retrieval methods for both codebase and expertise context.
* **Support for other VCS:** Extend to GitHub, GitLab, etc.
* **UI for Configuration:** A simple web UI for managing settings.
* **Direct Diff Analysis:** Instead of relying on text summaries of diffs, directly parse and analyze diff hunks for more precise feedback.
* **Automated Code Fix Suggestions:** Allow the bot to suggest concrete code changes.