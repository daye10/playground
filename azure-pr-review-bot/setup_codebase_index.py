import os
import logging
from typing import List, Set
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter 
from dotenv import load_dotenv
import argparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define relevant file extensions to include in the index
# You can customize this list based on your project
DEFAULT_RELEVANT_EXTENSIONS: Set[str] = {
    '.py', '.cs', '.js', '.ts', '.tsx', '.java', '.go', '.rb', '.php', '.cpp', '.c', '.h', '.hpp',
    '.html', '.css', '.scss', '.less', '.sql', '.md', '.json', '.yaml', '.yml', '.sh', '.bat',
    '.razor', '.cshtml' 
}
# Define directories to ignore
DEFAULT_IGNORE_DIRS: Set[str] = {
    '.git', '.vscode', '.idea', 'node_modules', 'bin', 'obj', 
    'dist', 'build', 'target', 'venv', 'env', '__pycache__', '.pytest_cache'
}


def load_and_split_project_files(project_path: str, 
                                 relevant_extensions: Set[str], 
                                 ignore_dirs: Set[str]) -> List[Document]:
    docs: List[Document] = []

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150) 
    logging.info(f"Scanning project path: {project_path}")
    for root, dirs, files in os.walk(project_path, topdown=True):
        # Modify dirs in-place to skip ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file_name in files:
            file_path = os.path.join(root, file_name)
            _, extension = os.path.splitext(file_name.lower()) # Use lower for extension matching

            if extension in relevant_extensions:
                relative_path = os.path.relpath(file_path, project_path)
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    if not content.strip(): # Skip empty or whitespace-only files
                        logging.debug(f"Skipping empty file: {relative_path}")
                        continue

                    # Split the content into chunks
                    split_texts = text_splitter.split_text(content)
                    for i, chunk in enumerate(split_texts):
                        # Store the relative path for easier reference
                        metadata = {"source_file": relative_path, "chunk_id": i}
                        docs.append(Document(page_content=chunk, metadata=metadata))
                    logging.info(f"Processed and chunked: {relative_path} ({len(split_texts)} chunks)")
                except Exception as e:
                    logging.warning(f"Could not read or process file {relative_path}: {e}")
    return docs

def build_codebase_index(project_path: str,
                         index_save_path: str = "codebase_context_index",
                         relevant_extensions: Set[str] = DEFAULT_RELEVANT_EXTENSIONS,
                         ignore_dirs: Set[str] = DEFAULT_IGNORE_DIRS):
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        logging.error("OPENAI_API_KEY not found in environment variables. Cannot create embeddings.")
        return

    if not os.path.isdir(project_path):
        logging.error(f"Provided project path is not a valid directory: {project_path}")
        return

    logging.info(f"Starting to build codebase index from local project path: {project_path}")

    documents = load_and_split_project_files(project_path, relevant_extensions, ignore_dirs)

    if not documents:
        logging.error("No documents were created from the project files. Index will not be built.")
        return

    logging.info(f"Creating embeddings for {len(documents)} document chunks. This may take a while...")
    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small") 

        vector_store = None
        docs_per_faiss_batch = 512 

        for i in range(0, len(documents), docs_per_faiss_batch):
            batch = documents[i:i + docs_per_faiss_batch]
            if not batch:
                continue
            
            logging.info(f"Processing batch {i // docs_per_faiss_batch + 1} with {len(batch)} documents for FAISS.")
            
            if vector_store is None:
                # Initialize the FAISS index with the first batch
                vector_store = FAISS.from_documents(batch, embeddings)
                logging.info(f"Initialized FAISS index with first batch of {len(batch)} documents.")
            else:
                # Add subsequent batches to the existing index.
                # This internally gets embeddings for the new batch and adds them.
                vector_store.add_documents(batch)
                logging.info(f"Added batch of {len(batch)} documents. Total documents processed so far: {i + len(batch)}")
            
            # Optional: if you suspect you might also hit requests-per-minute limits (different from tokens-per-request)
            # time.sleep(1) # Add a 1-second delay between batches

        if vector_store:
            vector_store.save_local(index_save_path)
            logging.info(f"Codebase FAISS index with {len(documents)} total chunks processed and saved to '{index_save_path}/'.")
        else:
            logging.error("Vector store was not created (e.g., no documents processed). Index not saved.")

    except Exception as e:
        logging.error(f"Failed to create or save FAISS index: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a FAISS index from a local project codebase.")
    parser.add_argument("project_path", type=str, help="The full local path to your project directory.")
    parser.add_argument("--index_path", type=str, default="codebase_context_index", help="Directory path to save the FAISS index.")
    parser.add_argument("--extensions", type=str, help="Comma-separated list of file extensions to include (e.g., .py,.js,.cs). Overrides defaults.")
    parser.add_argument("--ignore", type=str, help="Comma-separated list of directory names to ignore (e.g., .git,node_modules). Overrides defaults.")

    args = parser.parse_args()

    current_extensions = DEFAULT_RELEVANT_EXTENSIONS
    if args.extensions:
        current_extensions = {ext.strip().lower() for ext in args.extensions.split(',')}
    
    current_ignore_dirs = DEFAULT_IGNORE_DIRS
    if args.ignore:
        current_ignore_dirs = {d.strip() for d in args.ignore.split(',')}

    build_codebase_index(args.project_path, args.index_path, current_extensions, current_ignore_dirs)
    logging.info("Codebase index setup process finished.")