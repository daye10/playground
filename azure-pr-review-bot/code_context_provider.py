import os
import logging
import argparse
import time
from pathlib import Path
from typing import List, Set, Optional, Dict, Any

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from tqdm import tqdm

# --- Default Configurations ---
DEFAULT_RELEVANT_EXTENSIONS: Set[str] = {
    '.py', '.cs', '.js', '.ts', '.tsx', '.java', '.go', '.rb', '.php', '.cpp', '.c', '.h', '.hpp',
    '.html', '.css', '.scss', '.less', '.sql', '.md', '.json', '.yaml', '.yml', '.sh', '.bat',
    '.razor', '.cshtml', '.txt', '.ipynb' 
}
DEFAULT_IGNORE_DIRS: Set[str] = {
    '.git', '.vscode', '.idea', 'node_modules', 'bin', 'obj', 'dist', 'build',
    'target', 'venv', 'env', '__pycache__', '.pytest_cache', '.mypy_cache',
    'logs', 'temp', 'tmp', 'coverage', 'Temp', 'CentralODataClient.UnitTests',
    'MerchandisingODataClient.UnitTests', 'ODataClientUnitTestGenerator'
}
DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"
DEFAULT_CHUNK_SIZE: int = 1500
DEFAULT_CHUNK_OVERLAP: int = 150
DEFAULT_FAISS_BATCH_SIZE: int = 512 # Number of Langchain Documents to add to FAISS at a time

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class CodebaseIndexer:
    """
    A class to scan a project codebase, process relevant files,
    generate embeddings, and build a FAISS vector index.
    """

    def __init__(self,
                 project_path: str,
                 index_save_path: str = "codebase_faiss_index",
                 relevant_extensions: Optional[Set[str]] = None,
                 ignore_dirs: Optional[Set[str]] = None,
                 embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
                 docs_per_faiss_batch: int = DEFAULT_FAISS_BATCH_SIZE,
                 openai_api_key: Optional[str] = None):
        """
        Initializes the CodebaseIndexer.

        Args:
            project_path: The root directory of the project to index.
            index_save_path: Directory path to save the FAISS index.
            relevant_extensions: Set of file extensions to include. Defaults to DEFAULT_RELEVANT_EXTENSIONS.
            ignore_dirs: Set of directory names to ignore. Defaults to DEFAULT_IGNORE_DIRS.
            embedding_model_name: Name of the OpenAI embedding model to use.
            chunk_size: The maximum size of text chunks.
            chunk_overlap: The overlap between consecutive text chunks.
            docs_per_faiss_batch: Number of documents to process in each batch for FAISS indexing.
            openai_api_key: Optional OpenAI API key. If None, it will try to load from .env.
        """
        self.project_path = Path(project_path).resolve()
        self.index_save_path = Path(index_save_path).resolve()
        self.relevant_extensions = relevant_extensions if relevant_extensions is not None else DEFAULT_RELEVANT_EXTENSIONS
        self.ignore_dirs = ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
        self.embedding_model_name = embedding_model_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.docs_per_faiss_batch = docs_per_faiss_batch
        self.openai_api_key = openai_api_key

        self._validate_paths()
        self._load_api_key()

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )
        self.embeddings_model: Optional[OpenAIEmbeddings] = None

    def _validate_paths(self) -> None:
        """Validates the provided project path."""
        if not self.project_path.is_dir():
            logger.error(f"Project path '{self.project_path}' is not a valid directory.")
            raise ValueError(f"Invalid project path: {self.project_path}")
        logger.info(f"Project path validated: {self.project_path}")
        if not self.index_save_path.parent.is_dir():
             logger.warning(f"Parent directory for index path '{self.index_save_path.parent}' does not exist. It will be created if possible.")


    def _load_api_key(self) -> None:
        """Loads the OpenAI API key from environment variables or the provided argument."""
        if self.openai_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key
            logger.info("Using provided OpenAI API key.")
        else:
            load_dotenv()
            self.openai_api_key = os.getenv("OPENAI_API_KEY")

        if not self.openai_api_key:
            logger.error("OPENAI_API_KEY not found. Please set it in your .env file or pass it as an argument.")
            raise EnvironmentError("OPENAI_API_KEY not configured.")
        logger.info("OpenAI API key loaded successfully.")

    def _is_relevant_file(self, file_path: Path) -> bool:
        """Checks if a file should be processed based on its extension."""
        return file_path.is_file() and file_path.suffix.lower() in self.relevant_extensions

    def _get_files_to_process(self) -> List[Path]:
        """
        Scans the project directory and returns a list of relevant files to process.
        Skips ignored directories and non-relevant file types.
        """
        logger.info(f"Scanning for relevant files in '{self.project_path}'...")
        logger.info(f"Including extensions: {', '.join(self.relevant_extensions)}")
        logger.info(f"Ignoring directories: {', '.join(self.ignore_dirs)}")

        discovered_files: List[Path] = []
        paths_to_scan = list(self.project_path.rglob("*")) # Get all paths for tqdm

        for path_obj in tqdm(paths_to_scan, desc="Scanning project directory", unit="path"):
            if any(ignored_dir in path_obj.parts for ignored_dir in self.ignore_dirs):
                if path_obj.is_dir(): # To avoid processing files within an ignored dir if rglob gives them
                    # This logic is slightly different from os.walk's dirs[:] modification.
                    # With rglob, we check each path. If a parent is ignored, the child is ignored.
                    pass # Handled by the 'any' check above
                continue

            if self._is_relevant_file(path_obj):
                discovered_files.append(path_obj)
        
        logger.info(f"Found {len(discovered_files)} relevant files to process.")
        return discovered_files

    def _process_file_content(self, file_path: Path) -> List[Document]:
        """
        Reads a file's content and splits it into Document chunks.

        Args:
            file_path: Path to the file to process.

        Returns:
            A list of Document objects, or an empty list if the file cannot be processed.
        """
        relative_path_str = str(file_path.relative_to(self.project_path))
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            if not content.strip():
                logger.debug(f"Skipping empty file: {relative_path_str}")
                return []

            split_texts = self.text_splitter.split_text(content)
            documents = [
                Document(
                    page_content=chunk,
                    metadata={"source_file": relative_path_str, "chunk_id": i}
                ) for i, chunk in enumerate(split_texts)
            ]
            logger.debug(f"Processed and chunked '{relative_path_str}' into {len(documents)} chunks.")
            return documents
        except Exception as e:
            logger.warning(f"Could not read or process file '{relative_path_str}': {e}")
            return []

    def load_and_split_all_documents(self) -> List[Document]:
        """
        Loads content from all relevant files and splits them into Document chunks.
        """
        files_to_process = self._get_files_to_process()
        if not files_to_process:
            logger.warning("No relevant files found to process. Aborting index creation.")
            return []

        all_documents: List[Document] = []
        logger.info(f"Processing {len(files_to_process)} files...")

        for file_path in tqdm(files_to_process, desc="Processing files", unit="file"):
            documents = self._process_file_content(file_path)
            all_documents.extend(documents)

        if not all_documents:
            logger.error("No document chunks were created from the project files. Index will not be built.")
            return []

        logger.info(f"Successfully created {len(all_documents)} document chunks from {len(files_to_process)} files.")
        return all_documents

    def build_and_save_faiss_index(self, documents: List[Document]) -> None:
        """
        Builds a FAISS index from the given documents and saves it locally.

        Args:
            documents: A list of Document objects to index.
        """
        if not documents:
            logger.error("No documents provided to build the index. Aborting.")
            return

        logger.info(f"Initializing OpenAI Embeddings model: {self.embedding_model_name}")
        try:
            self.embeddings_model = OpenAIEmbeddings(
                model=self.embedding_model_name,
                api_key=self.openai_api_key # Explicitly pass the key
            )
        except Exception as e:
            logger.error(f"Failed to initialize OpenAIEmbeddings: {e}", exc_info=True)
            return

        logger.info(f"Creating FAISS index for {len(documents)} document chunks. This may take a while...")
        logger.info(f"Using FAISS batch size: {self.docs_per_faiss_batch} documents.")

        vector_store: Optional[FAISS] = None
        num_batches = (len(documents) + self.docs_per_faiss_batch - 1) // self.docs_per_faiss_batch

        try:
            for i in tqdm(range(0, len(documents), self.docs_per_faiss_batch),
                          desc="Building FAISS index", unit="batch", total=num_batches):
                batch_documents = documents[i : i + self.docs_per_faiss_batch]
                if not batch_documents:
                    continue

                if vector_store is None:
                    vector_store = FAISS.from_documents(batch_documents, self.embeddings_model)
                    logger.info(f"Initialized FAISS index with first batch of {len(batch_documents)} documents.")
                else:
                    vector_store.add_documents(batch_documents) 
                    logger.info(f"Added batch of {len(batch_documents)} documents to FAISS. Total documents processed so far: {i + len(batch_documents)}")
                
                # Optional: Add a small delay if hitting API rate limits (though OpenAIEmbeddings client handles some retry logic)
                # time.sleep(0.5) # Example: 0.5 second delay

            if vector_store:
                self.index_save_path.mkdir(parents=True, exist_ok=True) 
                vector_store.save_local(folder_path=str(self.index_save_path))
                logger.info(f"FAISS index with {len(documents)} total chunks successfully built and saved to '{self.index_save_path}'.")
            else:
                logger.error("Vector store was not created (e.g., no documents processed or first batch failed). Index not saved.")

        except Exception as e:
            logger.error(f"Failed to create or save FAISS index: {e}", exc_info=True)


    def run_indexing(self) -> None:
        """
        Orchestrates the entire indexing process:
        1. Loads and splits documents from the project.
        2. Builds and saves the FAISS index from these documents.
        """
        start_time = time.perf_counter()
        logger.info(f"Starting codebase indexing process for project: {self.project_path}")

        documents = self.load_and_split_all_documents()
        if not documents:
            logger.warning("No documents were generated. Exiting indexing process.")
            return

        self.build_and_save_faiss_index(documents)

        end_time = time.perf_counter()
        logger.info(f"Codebase indexing process finished in {end_time - start_time:.2f} seconds.")


def main():
    """Main function to handle command-line arguments and run the indexer."""
    parser = argparse.ArgumentParser(
        description="Build a FAISS vector index from a local project codebase for RAG applications.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "project_path",
        type=str,
        help="The full local path to your project directory."
    )
    parser.add_argument(
        "--index_path",
        type=str,
        default="codebase_faiss_index",
        help="Directory path to save the FAISS index."
    )
    parser.add_argument(
        "--extensions",
        type=str,
        help=(
            "Comma-separated list of file extensions to include (e.g., .py,.js,.cs). "
            f"Defaults to: {', '.join(sorted(list(DEFAULT_RELEVANT_EXTENSIONS))[:5])}..."
        )
    )
    parser.add_argument(
        "--ignore_dirs",
        type=str,
        help=(
            "Comma-separated list of directory names to ignore (e.g., .git,node_modules). "
            f"Defaults to: {', '.join(sorted(list(DEFAULT_IGNORE_DIRS))[:3])}..."
        )
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
        help="Name of the OpenAI embedding model to use."
    )
    parser.add_argument(
        "--chunk_size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Maximum size of text chunks for splitting documents."
    )
    parser.add_argument(
        "--chunk_overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Overlap between consecutive text chunks."
    )
    parser.add_argument(
        "--faiss_batch_size",
        type=int,
        default=DEFAULT_FAISS_BATCH_SIZE,
        help="Number of documents to add to FAISS in each batch."
    )
    parser.add_argument(
        "--api_key",
        type=str,
        default=None,
        help="OpenAI API key. If not provided, attempts to load from .env file (OPENAI_API_KEY)."
    )
    parser.add_argument(
        "--log_level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )

    args = parser.parse_args()

    # Set log level from CLI
    try:
        logging.getLogger().setLevel(args.log_level.upper())
        logger.info(f"Logging level set to {args.log_level.upper()}")
    except ValueError:
        logger.error(f"Invalid log level: {args.log_level}. Defaulting to INFO.")
        logging.getLogger().setLevel(logging.INFO)


    current_extensions = DEFAULT_RELEVANT_EXTENSIONS
    if args.extensions:
        current_extensions = {ext.strip().lower() for ext in args.extensions.split(',') if ext.strip()}
    
    current_ignore_dirs = DEFAULT_IGNORE_DIRS
    if args.ignore_dirs:
        current_ignore_dirs = {d.strip() for d in args.ignore_dirs.split(',') if d.strip()}

    try:
        indexer = CodebaseIndexer(
            project_path=args.project_path,
            index_save_path=args.index_path,
            relevant_extensions=current_extensions,
            ignore_dirs=current_ignore_dirs,
            embedding_model_name=args.model,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            docs_per_faiss_batch=args.faiss_batch_size,
            openai_api_key=args.api_key
        )
        indexer.run_indexing()
    except (ValueError, EnvironmentError) as e:
        logger.error(f"Initialization failed: {e}")
        # For specific exit codes if needed: sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred during the indexing process: {e}", exc_info=True)
        # sys.exit(1)

    logger.info("Codebase index setup process finished.")


if __name__ == "__main__":
    main()