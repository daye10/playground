import os
import logging
import argparse
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.chat_history import BaseChatMessageHistory
from langchain.memory import ChatMessageHistory 
from langchain_core.runnables.history import RunnableWithMessageHistory
from operator import itemgetter


# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Default Configurations ---
CODEBASE_INDEX_PATH = "codebase_faiss_index" 
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_CHAT_MODEL = "gpt-4o-mini" 
DEFAULT_NUM_CHUNKS = 5
DEFAULT_SESSION_ID = "cli_chat_session"


class CodeChatbot:
    """
    A chatbot for querying a codebase, using a FAISS vector index and conversational memory.
    """

    def __init__(self,
                 openai_api_key: str,
                 codebase_index_path: str = CODEBASE_INDEX_PATH,
                 embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
                 chat_model_name: str = DEFAULT_CHAT_MODEL,
                 num_codebase_context_chunks: int = DEFAULT_NUM_CHUNKS,
                 allow_dangerous_deserialization: bool = True): # For FAISS

        self.openai_api_key = openai_api_key
        self.codebase_index_path = Path(codebase_index_path)
        self.embedding_model_name = embedding_model_name
        self.chat_model_name = chat_model_name
        self.num_codebase_context_chunks = num_codebase_context_chunks
        self.allow_dangerous_deserialization = allow_dangerous_deserialization

        self.llm = ChatOpenAI(
            model=chat_model_name,
            api_key=openai_api_key,
            temperature=0.2,
            # streaming=True # Enable for streaming responses if UI supports it
        )
        self.embeddings_model = OpenAIEmbeddings(
            model=embedding_model_name,
            api_key=openai_api_key
        )

        self.codebase_retriever = self._load_retriever()
        self.message_histories: Dict[str, BaseChatMessageHistory] = {}

        self.runnable_with_history = self._build_rag_chain_with_history()
        logger.info("CodeChatbot initialized.")
        if not self.codebase_retriever:
             logger.warning(f"Codebase context index not found or failed to load from '{self.codebase_index_path}'. Contextual answers will be limited.")
        else:
            logger.info(f"Codebase retriever loaded successfully from '{self.codebase_index_path}'.")


    def _load_retriever(self) -> Optional[Any]:
        """Loads the FAISS vector store and returns a retriever instance."""
        if self.codebase_index_path.exists() and self.codebase_index_path.is_dir():
            try:
                vector_store = FAISS.load_local(
                    folder_path=str(self.codebase_index_path),
                    embeddings=self.embeddings_model,
                    allow_dangerous_deserialization=self.allow_dangerous_deserialization
                )
                return vector_store.as_retriever(
                    search_kwargs={"k": self.num_codebase_context_chunks}
                )
            except Exception as e:
                logger.error(f"Failed to load codebase context index from '{self.codebase_index_path}': {e}", exc_info=True)
                return None
        else:
            logger.warning(f"Codebase context index directory not found at '{self.codebase_index_path}'.")
            return None

    def _get_session_history(self, session_id: str) -> BaseChatMessageHistory:
        """Retrieves or creates a chat message history for a given session ID."""
        if session_id not in self.message_histories:
            self.message_histories[session_id] = ChatMessageHistory()
            logger.info(f"Created new in-memory chat history for session_id: {session_id}")
        return self.message_histories[session_id]

    def _format_docs(self, docs: List[Any]) -> str:
        """Formats retrieved documents into a string for the prompt context."""
        if not docs:
            return "No specific code context was retrieved from the codebase for this question."
        formatted_context = "\n\n---\n\n".join(
            f"From file: {doc.metadata.get('source_file', 'Unknown')}\n```\n{doc.page_content}\n```"
            for doc in docs
        )
        return formatted_context if formatted_context.strip() else "No specific code context was retrieved (empty after formatting)."

    def _build_rag_chain_with_history(self) -> RunnableWithMessageHistory:
        """Builds the RAG chain integrated with conversational history."""

        if self.codebase_retriever:
            contextualize_q_chain = (
                itemgetter("question") 
                | self.codebase_retriever
                | self._format_docs
            )
        else:
            contextualize_q_chain = RunnableLambda(
                lambda x: "Codebase index not available for context. Please ensure it's built and the path is correct."
            )

        prompt = ChatPromptTemplate.from_messages([
            MessagesPlaceholder(variable_name="history"), 
            ("user", """
Use the following retrieved context snippets from the codebase to help answer the current question.
If the context doesn't directly answer, try to infer an answer based on it, or state that the specific information isn't found in the provided snippets.
If asked for code generation or modification, use the context to inform your response and try to adhere to the coding style found in the context if possible.
Ensure your answer is directly addressing the user's question.

Retrieved Code Context for current question:
{context}

Current User Question: {question}
""")
        ])

        core_rag_chain = (
            RunnablePassthrough.assign(
                context=contextualize_q_chain, # Dynamically gets context
            ) # Passes through 'question' and 'history', adds 'context'
            | prompt
            | self.llm
            | StrOutputParser()
        )

        # Wrap the core RAG chain with history management
        runnable_with_history = RunnableWithMessageHistory(
            runnable=core_rag_chain,
            get_session_history=self._get_session_history,
            input_messages_key="question",  # The key in the input dict for the user's question
            history_messages_key="history", # The key in the prompt for history messages
            # output_messages_key="answer" # Optional: Key for AI's response in history (defaults to AIMessage content)
        )
        logger.info("RAG chain with history initialized.")
        return runnable_with_history

    def ask(self, question: str, session_id: str = DEFAULT_SESSION_ID) -> str:
        """
        Synchronously asks a question to the chatbot for a given session.
        """
        logger.info(f"Session '{session_id}' received question: {question}")
        if not self.runnable_with_history:
            logger.error("RAG chain is not initialized.")
            return "Chatbot is not properly initialized (RAG chain missing)."
        try:
            # The input to RunnableWithMessageHistory is a dictionary
            response = self.runnable_with_history.invoke(
                {"question": question},
                config={"configurable": {"session_id": session_id}}
            )
            return response
        except Exception as e:
            logger.error(f"Error during chatbot.ask for session '{session_id}': {e}", exc_info=True)
            return f"Error processing your question: {type(e).__name__} - {e}"

    async def ask_async(self, question: str, session_id: str = DEFAULT_SESSION_ID) -> str:
        """
        Asynchronously asks a question to the chatbot for a given session.
        """
        logger.info(f"Session '{session_id}' (async) received question: {question}")
        if not self.runnable_with_history:
            logger.error("RAG chain is not initialized for async.")
            return "Chatbot is not properly initialized (RAG chain missing)."
        try:
            response = await self.runnable_with_history.ainvoke(
                {"question": question},
                config={"configurable": {"session_id": session_id}}
            )
            return response
        except Exception as e:
            logger.error(f"Error during chatbot.ask_async for session '{session_id}': {e}", exc_info=True)
            return f"Error processing your question: {type(e).__name__} - {e}"

    def clear_history(self, session_id: str = DEFAULT_SESSION_ID) -> None:
        """Clears the chat history for a given session."""
        if session_id in self.message_histories:
            self.message_histories[session_id].clear()
            logger.info(f"Chat history cleared for session_id: {session_id}")
        else:
            logger.info(f"No active history found for session_id: {session_id} to clear.")


async def async_main_loop(bot: CodeChatbot):
    """Example of an asynchronous main loop."""
    logger.info("Async Code Chatbot initialized. Type 'exit' or 'quit' to end. Type 'clear' to reset history.")
    while True:
        try:
            user_question = await asyncio.to_thread(input, "Ask (async) > ") # Non-blocking input
            if user_question.lower() in ["exit", "quit"]:
                logger.info("Exiting async chatbot.")
                break
            if user_question.lower() == "clear":
                bot.clear_history(DEFAULT_SESSION_ID)
                print("Bot: Chat history cleared.\n")
                continue
            if not user_question.strip():
                continue

            answer = await bot.ask_async(user_question, session_id=DEFAULT_SESSION_ID)
            print(f"\nBot: {answer}\n")
        except KeyboardInterrupt:
            logger.info("Exiting async chatbot due to KeyboardInterrupt.")
            break
        except Exception as e:
            logger.error(f"An error occurred in the async chat loop: {e}", exc_info=True)
            print("Sorry, an unexpected error occurred.")


def main():
    """Main function to handle command-line arguments and run the chatbot."""
    parser = argparse.ArgumentParser(
        description="A chatbot for querying a codebase using a FAISS vector index.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--api_key",
        type=str,
        default=os.getenv("OPENAI_API_KEY"),
        help="OpenAI API key. Defaults to OPENAI_API_KEY environment variable."
    )
    parser.add_argument(
        "--index_path",
        type=str,
        default=os.getenv("CODEBASE_INDEX_PATH", CODEBASE_INDEX_PATH),
        help="Path to the FAISS codebase index directory."
    )
    parser.add_argument(
        "--embedding_model",
        type=str,
        default=os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="Name of the OpenAI embedding model."
    )
    parser.add_argument(
        "--chat_model",
        type=str,
        default=os.getenv("OPENAI_CHATBOT_MODEL", DEFAULT_CHAT_MODEL),
        help="Name of the OpenAI chat model."
    )
    parser.add_argument(
        "--num_chunks",
        type=int,
        default=int(os.getenv("NUM_CODEBASE_CONTEXT_CHUNKS_CHATBOT", DEFAULT_NUM_CHUNKS)),
        help="Number of codebase context chunks to retrieve."
    )
    parser.add_argument(
        "--allow_dangerous_deserialization",
        action=argparse.BooleanOptionalAction, # Allows --allow_dangerous_deserialization or --no-allow_dangerous_deserialization
        default=True,
        help="Allow dangerous deserialization when loading FAISS index (required for some index versions)."
    )
    parser.add_argument(
        "--log_level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )
    parser.add_argument(
        "--use_async_loop",
        default=False,
        help="Use an asynchronous main loop for interaction (experimental for CLI)."
    )

    args = parser.parse_args()

    # Set log level from CLI
    try:
        logging.getLogger().setLevel(args.log_level.upper())
        logger.info(f"Logging level set to {args.log_level.upper()}")
    except ValueError:
        logger.error(f"Invalid log level: {args.log_level}. Defaulting to INFO.")
        logging.getLogger().setLevel(logging.INFO)


    if not args.api_key:
        logger.error("OpenAI API key not found. Please provide it via --api_key or OPENAI_API_KEY environment variable.")
        return

    code_index_path_obj = Path(args.index_path)
    if not (code_index_path_obj.exists() and code_index_path_obj.is_dir()):
        logger.warning(
            f"Codebase index directory not found at '{args.index_path}'. "
            "The chatbot will run without codebase context. "
            "Build it first (e.g., using a script like setup_codebase_index.py)."
        )

    try:
        bot = CodeChatbot(
            openai_api_key=args.api_key,
            codebase_index_path=args.index_path,
            embedding_model_name=args.embedding_model,
            chat_model_name=args.chat_model,
            num_codebase_context_chunks=args.num_chunks,
            allow_dangerous_deserialization=args.allow_dangerous_deserialization
        )
    except Exception as e:
        logger.error(f"Failed to initialize CodeChatbot: {e}", exc_info=True)
        return

    if args.use_async_loop:
        try:
            asyncio.run(async_main_loop(bot))
        except KeyboardInterrupt:
            logger.info("Async chatbot stopped by user.")
        except Exception as e:
            logger.error(f"Critical error in async main execution: {e}", exc_info=True)
    else:
        logger.info("Code Chatbot initialized. Type 'exit' or 'quit' to end. Type 'clear' to reset history.")
        while True:
            try:
                user_question = input("Ask > ")
                if user_question.lower() in ["exit", "quit"]:
                    logger.info("Exiting chatbot.")
                    break
                if user_question.lower() == "clear":
                    bot.clear_history(DEFAULT_SESSION_ID)
                    print("Bot: Chat history cleared.\n")
                    continue
                if not user_question.strip():
                    continue

                answer = bot.ask(user_question, session_id=DEFAULT_SESSION_ID)
                print(f"\nBot: {answer}\n")
            except KeyboardInterrupt:
                logger.info("Exiting chatbot due to KeyboardInterrupt.")
                break
            except Exception as e:
                logger.error(f"An error occurred in the chat loop: {e}", exc_info=True)
                print("Sorry, an unexpected error occurred.")


if __name__ == "__main__":
    load_dotenv() 
    main()