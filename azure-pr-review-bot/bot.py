import os
import base64
import requests
import logging
import json
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta
from dotenv import load_dotenv

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough


from langchain_openai import ChatOpenAI, OpenAIEmbeddings  
from langchain_community.vectorstores import FAISS
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain


class PRReviewBot:
    """
    A bot to fetch open PRs from Azure DevOps, summarize them via LLM,
    rotate through a fixed set of 'primary' reviewers weekly, and
    suggest 'optional' reviewers via a retrieval-augmented LLM chain.
    
    .env requirements:
      AZURE_DEVOPS_ORG, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_REPO, AZURE_DEVOPS_PAT
      TEAMS_WEBHOOK_URL
      OPENAI_API_KEY
      REVIEWERS_LIST="alice@example.com,bob@example.com,charles@example.com,..."  # comma-separated
      NUM_PRIMARY_REVIEWERS=2
      NUM_OPTIONAL_REVIEWERS=1
      NUM_REVIEWERS=2  # for backwards compatibility in some chains
    """
    RR_STATE_FILE = "rr_state.json"
    
    def __init__(self):
        load_dotenv()
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s - %(levelname)s - %(message)s")
        # Azure DevOps + Teams
        self.org = os.getenv("AZURE_DEVOPS_ORG")
        self.project = os.getenv("AZURE_DEVOPS_PROJECT")
        self.repo = os.getenv("AZURE_DEVOPS_REPO")
        self.pat = os.getenv("AZURE_DEVOPS_PAT")
        self.teams_webhook = os.getenv("TEAMS_WEBHOOK_URL")
        self.num_reviewers = int(os.getenv("NUM_REVIEWERS", 2))
        
        # Primary+Optional reviewers config
        self.team_members: List[str] = [
            e.strip() for e in os.getenv("REVIEWERS_LIST", "").split(",") if e.strip()
        ]
        self.num_primary = int(os.getenv("NUM_PRIMARY_REVIEWERS", 2))
        self.num_optional = int(os.getenv("NUM_OPTIONAL_REVIEWERS", 1))

        if not all([self.org, self.project, self.repo, self.pat, self.teams_webhook]):
            logging.error("Missing Azure DevOps/Teams env vars. Exiting.")
            exit(1)
        if not os.getenv("OPENAI_API_KEY"):
            logging.error("Missing OPENAI_API_KEY. Exiting.")
            exit(1)
        if not self.team_members:
            logging.error("Missing REVIEWERS_LIST env var. Exiting.")
            exit(1)

        # Azure DevOps API
        auth = base64.b64encode(f":{self.pat}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        self.base_url = f"https://dev.azure.com/{self.org}/{self.project}/_apis/git/repositories/{self.repo}"

        # summarizer + RAG
        try:
            self.llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
            summarize_prompt = ChatPromptTemplate.from_template(
                'Write a concise summary of the following changes based on the files involved:\n"{text}"\nCONCISE SUMMARY:')
            self.summarizer = summarize_prompt | self.llm | StrOutputParser()

            # Expertise index
            embeddings = OpenAIEmbeddings()
            if not os.path.exists("expertise_index"):
                logging.error("expertise_index missing. Exiting.")
                exit(1)
            expert_index = FAISS.load_local("expertise_index", embeddings,
                                           allow_dangerous_deserialization=True)
            retriever = expert_index.as_retriever(search_kwargs={"k": self.num_optional + 2})
            recommend_prompt = ChatPromptTemplate.from_template(
                "You are an assistant helping assign code reviewers.\n\n"
                "Context:\n{context}\n\n"
                "Pull Request Summary:\n{input}\n\n"
                "Based ONLY on the provided context and the pull request summary, "
                "recommend {num_optionals} optional reviewers. List their unique names "
                "separated by commas. If none, say \"No optional reviewers found\".\n\n"
                "Recommended Reviewers:")
            combine_chain = create_stuff_documents_chain(self.llm, recommend_prompt)
            self.rag = create_retrieval_chain(retriever, combine_chain)
            logging.info("AI setup complete.")
        except Exception as e:
            logging.error(f"AI setup failed: {e}")
            exit(1)

    def _azure_get(self, url: str) -> Dict[str, Any]:
        try:
            r = requests.get(url, headers=self.headers, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logging.error(f"Azure API error ({url}): {e}")
            return {}

    def fetch_open_prs(self) -> List[Dict[str, Any]]:
        logging.info("Fetching active PRs from last 14 days...")
        url = f"{self.base_url}/pullrequests?searchCriteria.status=active&api-version=7.1-preview.1"
        data = self._azure_get(url).get("value", [])
        cutoff = datetime.utcnow() - timedelta(days=14)
        filtered = []
        for pr in data:
            cd = pr.get("creationDate", "")
            try:
                dt = datetime.fromisoformat(cd.rstrip("Z"))
                if dt >= cutoff:
                    filtered.append(pr)
            except:
                continue
        logging.info(f"{len(filtered)} PR(s) after filtering.")
        return filtered

    def fetch_diff_summary(self, pr_id: int) -> str:
        logging.info(f"Fetching diff for PR {pr_id}...")
        it = self._azure_get(f"{self.base_url}/pullrequests/{pr_id}/iterations?api-version=7.1-preview.1")
        vals = it.get("value", [])
        if not vals:
            return "Could not retrieve diff details."
        last = vals[-1]["id"]
        ch = self._azure_get(f"{self.base_url}/pullrequests/{pr_id}/iterations/{last}/changes?api-version=7.1-preview.1")
        entries = ch.get("changeEntries", [])
        parts = [
            f"- {e.get('changeType','edit').capitalize()} in file: {e.get('item',{}).get('path','Unknown')}"
            for e in entries
        ]
        if not parts:
            return "No specific file changes identified."
        summary = "\n".join(parts[:15])
        if len(parts) > 15:
            summary += f"\n- ... and {len(parts)-15} more changes."
        return summary

    def build_summary(self, pr: Dict[str, Any]) -> str:
        text = self.fetch_diff_summary(pr["pullRequestId"])
        if text.startswith("Could not"):
            return f"PR '{pr.get('title','')}' by {pr.get('createdBy',{}).get('displayName','Unknown')}. Details unavailable."
        try:
            return self.summarizer.invoke({"text": text})
        except Exception as e:
            logging.error(f"Summarization error for PR {pr['pullRequestId']}: {e}")
            return "Failed to generate summary."

    def pick_primary(self) -> List[str]:
        if os.path.exists(self.RR_STATE_FILE):
            st = json.load(open(self.RR_STATE_FILE))
        else:
            st = {"idx": 0}
        idx = st["idx"]
        primaries = [
            self.team_members[(idx + i) % len(self.team_members)]
            for i in range(self.num_primary)
        ]
        st["idx"] = (idx + self.num_primary) % len(self.team_members)
        json.dump(st, open(self.RR_STATE_FILE, "w"), indent=2)
        logging.info(f"Picked primary reviewers: {primaries}")
        return primaries

    def recommend_optional(self, summary: str) -> List[str]:
        try:
            out = self.rag.invoke({"input": summary, "num_optionals": self.num_optional})
            ans = out.get("answer", "")
            names = [n.strip() for n in ans.split(",") if n.strip() and "no optional" not in n.lower()]
            logging.info(f"Recommended optional reviewers: {names}")
            return names[: self.num_optional]
        except Exception as e:
            logging.error(f"Optional recommendation error: {e}")
            return []

    def notify_teams(self, primaries: List[str], pr_list: List[Tuple[str, str, List[str], str]]):
        header = f"👥 **Primary Reviewers This Week:** {', '.join(primaries)}\n\n"
        sections = []
        for title, summ, optional, url in pr_list:
            opt = f" (Optional: {optional[0]})" if optional else ""
            link = f"[{title}]({url})" if url else title
            snip = summ[:200] + "..." if len(summ) > 200 else summ
            sections.append(f"**{link}**{opt}\n> {snip}\n")
        body = header + "\n---\n".join(sections)
        payload = {"text": body}
        with open("teams_payload.json", "w") as f:
            json.dump(payload, f, indent=2)
        try:
            logging.info(f"Sending Teams notification...")
            logging.debug(f"Payload: {payload}")
            r = requests.post(self.teams_webhook, json=payload, timeout=15)
            r.raise_for_status()
            logging.info("Teams notification sent.")
        except Exception as e:
            logging.error(f"Teams notification failed: {e}")

    def run(self):
        logging.info("Starting PR review bot...")
        prs = self.fetch_open_prs()
        if not prs:
            requests.post(self.teams_webhook, json={"text": "✅ No open PRs!"}, timeout=10)
            return
        primary = self.pick_primary()
        pr_data: List[Tuple[str, str, List[str], str]] = []
        for pr in prs:
            tid = pr["pullRequestId"]
            title = pr.get("title", "Untitled PR")
            url = pr.get("_links", {}).get("web", {}).get("href", "")
            logging.info(f"Processing PR {tid}: '{title}'")
            summary = self.build_summary(pr)
            optional = self.recommend_optional(summary)
            pr_data.append((title, summary, optional, url))
        self.notify_teams(primary, pr_data)

if __name__ == "__main__":
    PRReviewBot().run()
