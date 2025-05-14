import os
import collections 
from typing import Dict, List, Tuple, DefaultDict

from dotenv import load_dotenv
from tqdm import tqdm

try:
    from utils import tokenize
except ImportError:
    print("Warning: 'utils.tokenize' not found. Using a basic placeholder tokenizer.")
    def tokenize(text: str) -> List[str]:
        """Basic placeholder tokenizer."""
        return text.lower().split()


load_dotenv()

TEXT_FILES_DIRECTORY = os.getenv('TEXT_DIR', './texts')

class Indexer:
    """
    Builds an inverted index from a collection of text files.

    The indexer processes text files from a specified directory, tokenizes
    their content, and constructs an inverted index mapping terms to a list
    of document IDs and term frequencies within those documents. It also
    calculates document lengths and overall corpus statistics like the total
    number of documents (N) and the average document length (avgdl).

    Attributes:
        inverted_index (Dict[str, List[Tuple[str, int]]]):
            The main inverted index. Maps each term (str) to a list of tuples,
            where each tuple contains a document ID (str, typically filename)
            and the term's frequency (int) in that document.
            Example: {"hello": [("doc1.txt", 2), ("doc2.txt", 1)]}

        doc_lengths (Dict[str, int]):
            A dictionary mapping each document ID (str) to its total number
            of tokens (int).
            Example: {"doc1.txt": 150, "doc2.txt": 200}

        N (int):
            The total number of documents processed in the collection.

        avgdl (float):
            The average document length (number of tokens) across all
            documents in the collection.
    """

    def __init__(self) -> None:
        """Initializes an empty Indexer."""
        # Maps: term -> list of (document_id, term_frequency_in_document)
        self.inverted_index: Dict[str, List[Tuple[str, int]]] = {}
        # Maps: document_id -> length_of_document (number of tokens)
        self.doc_lengths: Dict[str, int] = {}
        # Total number of documents in the collection
        self.N: int = 0
        # Average document length in the collection
        self.avgdl: float = 0.0

    def build_index(self, text_dir: str = TEXT_FILES_DIRECTORY) -> None:
        """
        Builds the inverted index from text files in the specified directory.

        This method reads all '.txt' files from the given directory,
        tokenizes their content, and populates the `inverted_index`,
        `doc_lengths`, `N`, and `avgdl` attributes.

        Args:
            text_dir (str): The path to the directory containing the text files.
                            Defaults to the `TEXT_FILES_DIRECTORY` constant, which
                            is sourced from the TEXT_DIR environment variable or
                            './texts'.

        """
        if not os.path.isdir(text_dir):
            raise FileNotFoundError(f"The specified directory does not exist: {text_dir}")


        intermediate_postings: DefaultDict[str, DefaultDict[str, int]] = \
            collections.defaultdict(lambda: collections.defaultdict(int))

        try:
            files_to_index = [
                filename for filename in os.listdir(text_dir)
                if filename.endswith('.txt') and os.path.isfile(os.path.join(text_dir, filename))
            ]
        except OSError as e:
            raise IOError(f"Error listing files in directory {text_dir}: {e}")


        self.N = len(files_to_index)
        if self.N == 0:
            print(f"Warning: No '.txt' files found in directory: {text_dir}. Index will be empty.")
            self.avgdl = 0.0 
            return

        total_document_length_sum = 0

        print(f"Starting indexing of {self.N} file(s) from '{text_dir}'...")
        for filename in tqdm(files_to_index, desc="Indexing progress"):
            document_id = filename 
            file_path = os.path.join(text_dir, filename)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    text_content = file.read()
            except IOError as e:
                print(f"Warning: Could not read file {file_path}. Skipping. Error: {e}")
                self.N -= 1 
                continue
            except Exception as e:
                print(f"Warning: An unexpected error occurred while reading {file_path}. Skipping. Error: {e}")
                self.N -= 1
                continue


            try:
                tokens = tokenize(text_content)
            except Exception as e:
                print(f"Warning: Tokenization failed for {document_id}. Skipping. Error: {e}")
                self.N -= 1
                continue


            self.doc_lengths[document_id] = len(tokens)
            total_document_length_sum += len(tokens)

            for token in tokens:
                intermediate_postings[token][document_id] += 1
        
        if self.N > 0:
            self.avgdl = total_document_length_sum / self.N
        else:
            print("Warning: No files were successfully processed. Index remains empty.")
            self.avgdl = 0.0
            self.doc_lengths.clear()
            self.inverted_index.clear()
            return


        # Convert the intermediate postings to the final inverted_index structure
        # final structure : term -> [(doc_id, tf), (doc_id, tf), ...]
        print("Finalizing inverted index structure...")
        for term, doc_freq_map in tqdm(intermediate_postings.items(), desc="Structuring index"):
            postings_list = list(doc_freq_map.items())
            
            postings_list.sort(key=lambda x: x[0])
            self.inverted_index[term] = postings_list
        
        print("Indexing complete.")
        print(f"  Total documents processed: {self.N}")
        print(f"  Total unique terms: {len(self.inverted_index)}")
        print(f"  Average document length: {self.avgdl:.2f} tokens")