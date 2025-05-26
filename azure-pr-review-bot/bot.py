import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Any, Optional

from dotenv import load_dotenv

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

from azure_devops_client import AzureDevOpsClient
from code_context_provider import CodeContextProvider 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Define a list of known placeholder/dummy GUID patterns to skip
DUMMY_GUID_PATTERNS = [
    "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
    "SKIP_THIS_USER",
]


class PRReviewBot:
    RR_STATE_FILE = "rr_state.json"

    def __init__(self):
        load_dotenv()
        self._load_config()
        self._validate_config()

        self.ad_client = AzureDevOpsClient(
            project_url=self.config["azure_devops_project_url"],
            repo_name=self.config["azure_devops_repo_name"],
            pat=self.config["azure_devops_pat"]
        )
        # CodeContextProvider is now primarily for fetching content of *files changed in the PR*
        self.code_context_provider = CodeContextProvider(
            self.ad_client,
            max_files=self.config["max_context_files"], # Max changed files to get live content for
            max_file_length=self.config["max_file_context_length"]
        )
        self._initialize_ai_components()
        logger.info("PRReviewBot initialized successfully.")

    def _load_config(self):
        self.config = {
            "azure_devops_project_url": os.getenv("AZURE_DEVOPS_PROJECT_URL"),
            "azure_devops_repo_name": os.getenv("AZURE_DEVOPS_REPO_NAME"),
            "azure_devops_pat": os.getenv("AZURE_DEVOPS_PAT"),
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "openai_model_name": os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "openai_embedding_model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            "reviewers_list_str": os.getenv("REVIEWERS_LIST"),
            "azure_devops_user_map_str": os.getenv("AZURE_DEVOPS_USER_MAP"),
            "num_primary_reviewers": int(os.getenv("NUM_PRIMARY_REVIEWERS", 2)),
            "num_optional_reviewers": int(os.getenv("NUM_OPTIONAL_REVIEWERS", 1)),
            "num_codebase_context_chunks": int(os.getenv("NUM_CODEBASE_CONTEXT_CHUNKS", 3)), 
            "days_to_consider_prs": int(os.getenv("DAYS_TO_CONSIDER_PRS", 14)),
            "enable_pr_comments": os.getenv("ENABLE_PR_COMMENTS", "true").lower() == "true",
            "test_pr_id": os.getenv("TEST_PR_ID"),
            "max_context_files": int(os.getenv("MAX_CONTEXT_FILES", 5)), 
            "max_file_context_length": int(os.getenv("MAX_FILE_CONTEXT_LENGTH", 2000)),
            "codebase_index_path": os.getenv("CODEBASE_INDEX_PATH", "codebase_context_index"),
        }
        self.team_members_emails = [
            e.strip() for e in self.config["reviewers_list_str"].split(",") if e.strip()
        ] if self.config["reviewers_list_str"] else []
        
        self.user_map_email_to_id: Dict[str, str] = {}
        if self.config["azure_devops_user_map_str"]:
            try:
                self.user_map_email_to_id = json.loads(self.config["azure_devops_user_map_str"])
            except json.JSONDecodeError:
                logger.error("Failed to parse AZURE_DEVOPS_USER_MAP. Ensure it's valid JSON.")
        logger.info("Configuration loaded.")

    def _validate_config(self):
        required_vars = [
            "azure_devops_project_url", "azure_devops_repo_name", "azure_devops_pat",
            "openai_api_key", "reviewers_list_str" 
            # azure_devops_user_map_str is not strictly required if no assignments needed
        ]
        missing_vars = [var for var in required_vars if not self.config[var]]
        if missing_vars:
            msg = f"Missing critical environment variables: {', '.join(missing_vars)}. Exiting."
            logger.error(msg)
            raise ValueError(msg)
        if not self.team_members_emails and self.config["reviewers_list_str"]: # check if string was there but parsing failed
            msg = "REVIEWERS_LIST is present but resulted in no team members. Check formatting. Exiting."
            logger.error(msg)
            raise ValueError(msg)
        elif not self.team_members_emails:
             logger.warning("REVIEWERS_LIST is empty or not configured. Primary reviewer assignment will be skipped.")

        logger.info("Configuration validated.")


    def _initialize_ai_components(self):
        try:
            self.llm = ChatOpenAI(
                model=self.config["openai_model_name"],
                temperature=1,
                api_key=self.config["openai_api_key"]
            )
            self.embeddings_model = OpenAIEmbeddings(
                model=self.config["openai_embedding_model"],
                api_key=self.config["openai_api_key"]
            )

            summarize_prompt_template = (
                "Concisely summarize the main purpose and nature of the changes based on the following PR diff summary. "
                "Focus on what changed, not on code quality yet.\n\n"
                "Pull Request Diff Summary:\n{diff_summary}\n\n"
                "Brief PR Summary:"
            )
            summarize_prompt = ChatPromptTemplate.from_template(summarize_prompt_template)
            self.summarizer_chain = summarize_prompt | self.llm | StrOutputParser()


            expertise_index_path = "expertise_index"
            if not os.path.exists(expertise_index_path):
                logger.error(f"Expertise index ('{expertise_index_path}/') not found. Please run setup_expertise_index.py. Reviewer recommendation will be impaired.")
                self.expert_retriever = None
            else:
                expert_vector_store = FAISS.load_local(
                    expertise_index_path,
                    self.embeddings_model,
                    allow_dangerous_deserialization=True
                )
                self.expert_retriever = expert_vector_store.as_retriever(
                    search_kwargs={"k": self.config["num_optional_reviewers"] + 3}
                )
            recommend_prompt_template = (
                "You are an assistant helping assign code reviewers. Based ONLY on the provided context (past PRs reviewed by individuals) "
                "and the current Pull Request's AI-generated summary, recommend {num_optionals} suitable optional reviewers. "
                "List their unique names (emails if available in context) separated by commas. "
                "If no suitable reviewers are found from the context, state 'No optional reviewers found'.\n\n"
                "Context from Past PRs:\n{context}\n\n"
                "Current Pull Request AI Summary:\n{input}\n\n"
                "Recommended Optional Reviewers (comma-separated emails/uniqueNames):"
            )
            recommend_prompt = ChatPromptTemplate.from_template(recommend_prompt_template)
            combine_docs_chain_reviewers = create_stuff_documents_chain(self.llm, recommend_prompt)
            if self.expert_retriever:
                self.rag_chain_reviewer = create_retrieval_chain(self.expert_retriever, combine_docs_chain_reviewers)
            else:
                self.rag_chain_reviewer = None # Or a chain that always returns "no recommendation"

            self.codebase_context_retriever = None
            codebase_index_path = self.config["codebase_index_path"]
            if os.path.exists(codebase_index_path):
                try:
                    codebase_vector_store = FAISS.load_local(
                        codebase_index_path,
                        self.embeddings_model,
                        allow_dangerous_deserialization=True
                    )
                    self.codebase_context_retriever = codebase_vector_store.as_retriever(
                        search_kwargs={"k": self.config["num_codebase_context_chunks"]}
                    )
                    logger.info(f"Successfully loaded codebase context index from '{codebase_index_path}'.")
                except Exception as e:
                    logger.error(f"Failed to load codebase context index from '{codebase_index_path}': {e}. Detailed context from codebase will be limited.", exc_info=True)
            else:
                logger.warning(f"Codebase context index not found at '{codebase_index_path}'. Detailed context from local codebase will be limited.")


            # Chain for Detailed Code Review/Analysis
            detailed_review_prompt_template = (
                "You are an expert C# and .NET code reviewer. Your task is to provide a DETAILED REVIEW focusing ONLY on the SPECIFIC MODIFICATIONS introduced in a Pull Request. "
                "You will be given:\n"
                "1. An OVERALL PR SUMMARY (describing the PR's general purpose).\n"
                "2. A SUMMARY OF FILE CHANGES (listing files added/edited/deleted).\n"
                "3. The FULL CONTENT OF FILES CHANGED IN THIS PR (use this to see the code in its final state for changed files).\n"
                "4. RELEVANT SNIPPETS FROM THE BROADER PROJECT CODEBASE (use this SOLELY to understand if the PR's changes are stylistically and architecturally COHERENT with the existing project).\n\n"

                "OVERALL PR SUMMARY:\n{ai_summary}\n\n"
                "SUMMARY OF FILE CHANGES (OPERATIONS):\n{diff_summary}\n\n" # This tells you which files were touched
                "CODE CONTEXT:\n"
                "1. FULL CONTENT OF FILES CHANGED IN THIS PR:\n{changed_files_content}\n"
                "   **IMPORTANT: Your review must focus on the DIFFERENCES and NEW CODE introduced by the author within these files. Do not review unchanged lines or sections unless the new code directly impacts them negatively.**\n"
                "2. RELEVANT SNIPPETS FROM THE BROADER PROJECT CODEBASE (for coherence check only):\n{local_codebase_rag_context}\n\n"

                "--- CRITICAL REVIEW INSTRUCTIONS ---\n"
                "A. **STRICT FOCUS ON PR MODIFICATIONS:** Your entire detailed review must be about the code that was **added, deleted, or modified by the author in THIS specific PR**. Refer to the 'SUMMARY OF FILE CHANGES' to understand the scope. When looking at the 'FULL CONTENT OF FILES CHANGED', identify the actual changes. Do NOT comment on or suggest improvements for pre-existing code that was NOT touched by this PR's author, unless the PR's changes introduce a direct conflict or bug with that existing code.\n"
                "B. **VERIFY CHANGES BEFORE COMMENTING:** Before suggesting an issue (e.g., missing event unsubscription, incorrect parameter passing), critically assess if this is related to a change MADE IN THIS PR. For instance, if the 'OVERALL PR SUMMARY' or 'SUMMARY OF FILE CHANGES' does not mention adding localization features, do not critique the lack of `IStringLocalizer` parameters as if they were expected from *this* PR.\n"
                "C. **CONTEXT FOR COHERENCE, NOT CRITIQUE:** Use the 'RELEVANT SNIPPETS FROM THE BROADER PROJECT CODEBASE' *only* to judge if the PR's changes fit well with existing patterns, style, and architecture. DO NOT critique these snippets themselves.\n"
                "D. **NO FORCED FEEDBACK / ACKNOWLEDGE GOOD INTEGRATION:** If the PR's modifications are minor, well-executed, and integrate coherently with the existing codebase without introducing issues, **explicitly state this (e.g., 'The PR's modifications are clear, directly address the stated purpose, and integrate well with existing patterns. No specific issues noted in the changes.').** Do not invent issues or suggestions if none are warranted by the *actual changes*.\n"
                "E. **Actionable & Specific:** If you do find issues *within the PR's modifications*, make your feedback specific, actionable, and polite. Reference file paths where appropriate.\n"
                "F. **Structure:** Organize feedback under 'Observations on PR Modifications:', 'Coherence with Existing Codebase:', 'Questions for Author (related to PR changes):'.\n\n"

                "DETAILED OBSERVATIONS (Strictly limited to the modifications in this PR):"
            )

            detailed_review_prompt = ChatPromptTemplate.from_template(detailed_review_prompt_template)
            self.detailed_reviewer_chain = detailed_review_prompt | self.llm | StrOutputParser()
            logger.info("AI components initialized successfully.")

        except Exception as e:
            logger.error(f"Failed to initialize AI components: {e}", exc_info=True)
            raise

    def get_pr_diff_and_changed_files(self, pr_id: int, iteration_id: int) -> Tuple[str, List[str]]:
        logger.info(f"Fetching diff summary for PR ID: {pr_id}, Iteration ID: {iteration_id}")
        changes_data = self.ad_client.get_iteration_changes(pr_id, iteration_id)
        if not changes_data or "changeEntries" not in changes_data:
            logger.warning(f"Could not retrieve change entries for PR {pr_id}, iteration {iteration_id}.")
            return "Could not retrieve diff details for this PR iteration.", []
        diff_summary_parts = []
        changed_file_paths = []
        for change in changes_data.get("changeEntries", []):
            item = change.get("item", {})
            path = item.get("path")
            change_type = change.get("changeType", "edit").capitalize()
            if path and item.get("objectId"):
                logger.debug(f"PR {pr_id} - Identified file change: {change_type} in {path}")
                changed_file_paths.append(path)
                diff_summary_parts.append(f"- {change_type} in file: {path}")
            else:
                logger.debug(f"PR {pr_id} - Skipping change entry, not identified as a processable file blob: {item.get('path', 'Unknown path')}")
        if not diff_summary_parts:
            logger.warning(f"PR {pr_id} - No specific file changes identified after processing changeEntries.")
            return "No specific file changes identified in this PR iteration.", changed_file_paths
        max_diff_lines = 20 
        diff_text = "\n".join(diff_summary_parts[:max_diff_lines])
        if len(diff_summary_parts) > max_diff_lines:
            diff_text += f"\n... and {len(diff_summary_parts) - max_diff_lines} more changes."
        logger.info(f"Generated diff summary for PR {pr_id}. {len(changed_file_paths)} files changed.")
        return diff_text, changed_file_paths

    def generate_brief_ai_summary(self, pr_id: int, diff_summary: str) -> str:
        logger.info(f"Generating brief AI summary for PR {pr_id} based on diff summary...")
        if diff_summary.startswith("Could not retrieve") or diff_summary.startswith("No specific file changes"):
            return "PR Summary: Basic information about file operations could not be determined."
        try:
            ai_summary = self.summarizer_chain.invoke({"diff_summary": diff_summary})
            logger.info(f"Successfully generated brief AI summary for PR {pr_id}.")
            return ai_summary
        except Exception as e:
            logger.error(f"Error generating brief AI summary for PR {pr_id}: {e}", exc_info=True)
            return "Brief AI summary generation failed."

    # New method to get context from the local codebase index
    def get_context_from_codebase_index(self, query_text: str) -> str:
        if not self.codebase_context_retriever:
            logger.info("Codebase context retriever not available. Skipping context retrieval from local index.")
            return "Local codebase context index not loaded or unavailable."
        if not query_text or not query_text.strip() :
            logger.info("Query text for codebase index is empty or invalid. Skipping retrieval.")
            return "No query text provided for local codebase context retrieval."
        try:
            k_chunks = self.config["num_codebase_context_chunks"]
            logger.info(f"Querying local codebase index with k={k_chunks} for context related to: '{query_text[:150].replace(os.linesep, ' ')}...'")
            relevant_docs = self.codebase_context_retriever.invoke(query_text)
            if not relevant_docs:
                logger.info("No relevant documents found in the local codebase index for the query.")
                return "No additional relevant context found in the local project codebase for this query."
            context_str = "" # Start with empty string, it will be prepended with a header in perform_detailed_review
            for i, doc in enumerate(relevant_docs):
                source_file = doc.metadata.get('source_file', 'Unknown file')
                chunk_id = doc.metadata.get('chunk_id', 'N/A')
                context_str += f"\nRelevant Snippet {i+1} (from: {source_file}, chunk: {chunk_id}):\n```\n{doc.page_content}\n```\n"
            return context_str
        except Exception as e:
            logger.error(f"Error querying codebase context index: {e}", exc_info=True)
            return "Error retrieving context from the local project codebase index."

    def perform_detailed_review(self, pr_id: int, brief_ai_summary: str, diff_summary: str, 
                                changed_files_live_content: str, # Content of *actually changed* files
                                changed_file_paths: List[str]) -> str:
        logger.info(f"Performing detailed review for PR {pr_id}...")

        # Determine query for local codebase RAG: use content of changed files if available, else diff summary
        query_for_local_index = changed_files_live_content
        if not query_for_local_index or query_for_local_index.startswith("No source code context could be retrieved"):
            query_for_local_index = diff_summary
            if query_for_local_index.startswith("Could not retrieve") or query_for_local_index.startswith("No specific file changes"):
                query_for_local_index = brief_ai_summary


        local_codebase_rag_context = "Local codebase context was not queried or yielded no results."
        if query_for_local_index and not (query_for_local_index.startswith("Could not retrieve") or query_for_local_index.startswith("No specific file changes") or query_for_local_index.startswith("PR Summary: Basic information")):
            local_codebase_rag_context = self.get_context_from_codebase_index(query_for_local_index)
        else:
            logger.warning(f"PR {pr_id}: Skipping local codebase RAG context retrieval due to lack of suitable query text (based on PR changes/summary).")


        try:
            review_input = {
                "ai_summary": brief_ai_summary,
                "diff_summary": diff_summary,
                "changed_files_content": changed_files_live_content,
                "local_codebase_rag_context": local_codebase_rag_context
            }
            detailed_observations = self.detailed_reviewer_chain.invoke(review_input)
            logger.info(f"PR {pr_id}: Successfully generated detailed observations.")
            return detailed_observations
        except Exception as e:
            logger.error(f"PR {pr_id}: Error during detailed review: {e}", exc_info=True)
            return "Detailed review could not be completed due to an internal error."

    def pick_primary_reviewers(self) -> List[str]:
        if not self.team_members_emails:
            logger.warning("No team members available for primary reviewer assignment.")
            return []
        try:
            state = {"idx": 0}
            if os.path.exists(self.RR_STATE_FILE):
                with open(self.RR_STATE_FILE, "r") as f:
                    state = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load round-robin state from {self.RR_STATE_FILE}, starting from index 0. Error: {e}")
            state = {"idx": 0}
        current_idx = state.get("idx", 0)
        num_team_members = len(self.team_members_emails)
        primary_reviewers_emails = []
        if num_team_members > 0:
            for i in range(self.config["num_primary_reviewers"]):
                reviewer_idx = (current_idx + i) % num_team_members
                primary_reviewers_emails.append(self.team_members_emails[reviewer_idx])
        state["idx"] = (current_idx + self.config["num_primary_reviewers"]) % num_team_members if num_team_members > 0 else 0
        try:
            with open(self.RR_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save round-robin state to {self.RR_STATE_FILE}: {e}")
        logger.info(f"Picked primary reviewers (emails): {primary_reviewers_emails}")
        return primary_reviewers_emails

    def recommend_optional_reviewers(self, brief_ai_summary_for_pr: str) -> List[str]:
        if not self.rag_chain_reviewer:
            logger.warning("Reviewer RAG chain not initialized. Skipping optional reviewer recommendation.")
            return []
        if not brief_ai_summary_for_pr or brief_ai_summary_for_pr.startswith("PR Summary: Basic information") or brief_ai_summary_for_pr.startswith("Brief AI summary generation failed"):
            logger.warning("Cannot recommend optional reviewers: Brief AI summary is unavailable or failed.")
            return []
        try:
            rag_input = { "input": brief_ai_summary_for_pr, "num_optionals": self.config["num_optional_reviewers"] }
            result = self.rag_chain_reviewer.invoke(rag_input)
            answer = result.get("answer", "")
            if "No optional reviewers found" in answer or not answer:
                logger.info("RAG chain found no suitable optional reviewers.")
                return []
            recommended_emails = [name.strip() for name in answer.split(",") if name.strip()]
            logger.info(f"Recommended optional reviewers (emails/uniqueNames from RAG): {recommended_emails}")
            return recommended_emails[:self.config["num_optional_reviewers"]] 
        except Exception as e:
            logger.error(f"Error recommending optional reviewers: {e}", exc_info=True)
            return []

    def _get_user_id_from_email(self, email: str) -> Optional[str]:
        user_id = self.user_map_email_to_id.get(email)
        if not user_id:
            logger.warning(f"No Azure DevOps User ID found in map for email: {email}")
            return None
        if user_id in DUMMY_GUID_PATTERNS:
            logger.info(f"Skipping assignment for email {email} as its ID '{user_id}' matches a known dummy/placeholder pattern.")
            return None 
        return user_id
        
    def run(self):
        logger.info("Starting PR Review Bot run...")
        active_prs_raw = self.ad_client.get_active_pull_requests()
        if not active_prs_raw:
            logger.info("No active PRs found.")
            return

        cutoff_date = datetime.utcnow() - timedelta(days=self.config["days_to_consider_prs"])
        processable_prs = []
        for pr_data in active_prs_raw:
            pr_id = pr_data.get("pullRequestId")
            if self.config["test_pr_id"] and str(pr_id) != self.config["test_pr_id"]:
                continue 
            creation_date_str = pr_data.get("creationDate")
            try:
                creation_date = datetime.fromisoformat(creation_date_str.replace("Z", "+00:00"))
                if creation_date >= cutoff_date.replace(tzinfo=creation_date.tzinfo): 
                    processable_prs.append(pr_data)
            except Exception as e:
                logger.warning(f"Could not parse creation date for PR {pr_id} ('{creation_date_str}'): {e}")
                continue
        
        if self.config["test_pr_id"] and not processable_prs:
             logger.info(f"Test PR ID {self.config['test_pr_id']} was specified but not found or not eligible.")
             return
        elif not processable_prs:
            logger.info("No PRs found matching the date criteria.")
            return
        logger.info(f"Found {len(processable_prs)} PR(s) to process.")

        primary_reviewer_emails_for_cycle = self.pick_primary_reviewers()

        for pr_data in processable_prs:
            pr_id = pr_data.get("pullRequestId")
            pr_title = pr_data.get("title", "Untitled PR")
            pr_created_by_email = pr_data.get("createdBy", {}).get("uniqueName") 
            logger.info(f"Processing PR {pr_id}: '{pr_title}'")

            iterations = self.ad_client.get_pull_request_iterations(pr_id)
            if not iterations: 
                logger.warning(f"No iterations found for PR {pr_id}. Skipping.")
                continue
            latest_iteration_id = iterations[-1].get("id")
            if not latest_iteration_id: 
                logger.warning(f"Could not get latest iteration ID for PR {pr_id}. Skipping.")
                continue

            # 1. Get summary of changed file operations
            diff_summary, changed_file_paths = self.get_pr_diff_and_changed_files(pr_id, latest_iteration_id)

            # 2. Get content of *actually changed files* in the PR (live from ADO)
            # `context_files_fetched_live` is the list of paths for which content was successfully fetched by CodeContextProvider
            changed_files_live_content, context_files_fetched_live = self.code_context_provider.get_relevant_code_context(
                pr_id, latest_iteration_id
            )

            # 3. Generate a brief AI summary (used for reviewer RAG and as input to detailed review)
            brief_ai_summary = self.generate_brief_ai_summary(pr_id, diff_summary)
            
            # 4. Perform detailed review using combined context
            detailed_observations = self.perform_detailed_review(
                pr_id,
                brief_ai_summary,
                diff_summary,
                changed_files_live_content,
                changed_file_paths # Pass this for potential use in perform_detailed_review if needed later
            )

            # 5. Post to PR 
            if self.config["enable_pr_comments"]:
                comment_to_post = f"**✨ AI PR Analysis ✨**\n\n"
                comment_to_post += f"**Brief Summary:**\n{brief_ai_summary}\n\n---\n\n"
                comment_to_post += f"**🤖 Detailed Observations & Review Pointers 🤖**\n\n{detailed_observations}\n\n---\n"
                
                if context_files_fetched_live:
                    comment_to_post += f"_Context for this analysis included content from changed files: {', '.join(context_files_fetched_live)} "
                    if self.codebase_context_retriever:
                        comment_to_post += "and relevant snippets from the broader project codebase._\n"
                    else:
                        comment_to_post += "._\n"
                elif self.codebase_context_retriever:
                     comment_to_post += "_Context for this analysis included relevant snippets from the broader project codebase._\n"

                comment_to_post += "_Please review all changes thoroughly. This AI feedback is for assistance only and may not be exhaustive or perfectly accurate._"
                
                self.ad_client.create_pr_thread_comment(pr_id, comment_to_post)
                logger.info(f"Posted AI summary and detailed observations to PR {pr_id}.")
            else:
                logger.info(f"PR commenting disabled. Brief Summary for PR {pr_id}:\n{brief_ai_summary}")
                logger.info(f"PR commenting disabled. Detailed Observations for PR {pr_id}:\n{detailed_observations}")

            bot_selected_reviewers_for_pr = []
            current_pr_primary_emails_filtered = [
                email for email in primary_reviewer_emails_for_cycle if email != pr_created_by_email
            ]
            for email in current_pr_primary_emails_filtered:
                user_id = self._get_user_id_from_email(email)
                if user_id and not any(r["id"] == user_id for r in bot_selected_reviewers_for_pr):
                    bot_selected_reviewers_for_pr.append({"id": user_id, "isRequired": False})
            
            optional_reviewer_emails_rag = self.recommend_optional_reviewers(brief_ai_summary)
            num_optionals_added_this_run = 0
            for email in optional_reviewer_emails_rag:
                if num_optionals_added_this_run >= self.config["num_optional_reviewers"]: break
                if email != pr_created_by_email and email not in current_pr_primary_emails_filtered:
                    user_id = self._get_user_id_from_email(email)
                    if user_id and not any(r["id"] == user_id for r in bot_selected_reviewers_for_pr):
                         bot_selected_reviewers_for_pr.append({"id": user_id, "isRequired": False})
                         num_optionals_added_this_run +=1
            
            if bot_selected_reviewers_for_pr:
                logger.info(f"Attempting to assign/update reviewers for PR {pr_id} (all as optional): {bot_selected_reviewers_for_pr}")
                current_pr_details = self.ad_client.get_pull_request_details(pr_id)
                existing_reviewers_on_pr_map = {}
                if current_pr_details and "reviewers" in current_pr_details:
                    for r_obj in current_pr_details["reviewers"]:
                        existing_reviewers_on_pr_map[r_obj["id"]] = {"id": r_obj["id"], "isRequired": r_obj.get("isRequired", False)}
                final_reviewers_map = existing_reviewers_on_pr_map.copy()
                for bot_rev in bot_selected_reviewers_for_pr:
                    final_reviewers_map[bot_rev["id"]] = {"id": bot_rev["id"], "isRequired": False} # Ensure bot-added are optional
                final_reviewers_payload = list(final_reviewers_map.values())
                if final_reviewers_payload:
                    self.ad_client.update_pr_reviewers(pr_id, final_reviewers_payload)
                else: logger.info(f"No valid reviewers to assign for PR {pr_id} after filtering.")
            else: logger.info(f"No reviewers selected by the bot for PR {pr_id}.")
            # --- End of Reviewer Assignment ----
            
        logger.info("PR Review Bot run completed.")

if __name__ == "__main__":
    bot = PRReviewBot()
    bot.run()