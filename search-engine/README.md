# TextSearch++: CLI Search Engine & Autocomplete

A simple Python CLI app that indexes a collection of local text files and provides functionalities for searching and exploring that content. It features BM25-ranked search, boolean AND search with skip-pointer optimization, and prefix-based autocomplete suggestions for search terms.

**TODO**: Implement wildcards search
## Features

* **Local File Indexing:** Indexes `.txt` files from a specified directory.
* **BM25 Ranked Search:** Retrieves documents based on relevance using the Okapi BM25 algorithm.
* **Boolean AND Search:** Finds documents containing all specified query terms, utilizing skip-pointer optimization for efficient postings list intersection.
* **Autocomplete Suggestions:** Offers prefix-based suggestions for search terms, powered by a Trie data structure and using term document frequency as a ranking heuristic.

## How It Works

* **Indexing (`indexer.py`):**
    * Reads text files from the specified directory.
    * Tokenizes the content (using NLTK's `punkt` tokenizer via a `utils.tokenize` function).
    * Builds an **inverted index** mapping each term to a list of documents it appears in, along with the term frequency in each document (`term -> list of (doc_id, tf)`).
    * Calculates document lengths and overall corpus statistics (total number of documents `N`, average document length `avgdl`).
* **Search Engine (`search.py`):**
    * **BM25 Ranking:** Implements the Okapi BM25 formula to score and rank documents based on a query. It utilizes pre-calculated IDF scores.
    * **Boolean AND:** Efficiently intersects sorted postings lists (document IDs) for given terms using a skip-pointer optimization to find documents containing all terms.
* **Autocomplete (`autocomplete.py`):**
    * Uses a **Trie** data structure to store all unique terms from the indexed documents.
    * Each node in the Trie corresponding to a prefix stores the top-k most frequent terms (based on document frequency) that start with that prefix.
    * When a user types a prefix, the system traverses the Trie to fetch these pre-computed suggestions.

## Setup
  **Prepare Text Files:**
    * Create a directory to store your `.txt` files that you want to index. By default, the application looks for a directory named `texts` in the same location as `main.py`.
    * Place your text documents (with `.txt` extension) into this directory.

  **Set Environment Variable (Optional but Recommended):**
    * Create a file named `.env` in the root directory of the project.
    * To specify a custom directory for your text files, add the following line to the `.env` file:
        ```
        TEXT_DIR=/path/to/your/text_files_directory
        ```
        If `TEXT_DIR` is not set, the application defaults to `./texts`.

