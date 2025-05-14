import math
from indexer import Indexer 
from typing import List, Dict, Tuple, Set, 

class SearchEngine:
    """
    A search engine that uses an inverted index from an Indexer object
    to perform document retrieval based on BM25 ranking and boolean AND queries.
    """

    # Default BM25 parameters
    DEFAULT_K1: float = 1.5
    DEFAULT_B: float = 0.75

    def __init__(self, indexer: Indexer):
        """
        Initializes the SearchEngine with data from an Indexer.

        Args:
            indexer: An instance of the Indexer class. It is expected to have
                     the attributes: `inverted_index`, `doc_lengths`, `N` (total
                     number of documents), and `avgdl` (average document length).
        """

        self.inverted_index: Dict[str, List[Tuple[str, int]]] = indexer.inverted_index
        self.document_lengths: Dict[str, int] = indexer.doc_lengths
        self.total_docs_in_collection: int = indexer.N
        self.average_doc_length: float = indexer.avgdl


        for attr_name in ['inverted_index', 'doc_lengths', 'N', 'avgdl']:
            if not hasattr(indexer, attr_name):
                raise AttributeError(f"The provided 'indexer' object is missing the required attribute: '{attr_name}'")
        if self.total_docs_in_collection < 0:
            raise ValueError("Indexer's 'N' (total documents) cannot be negative.")
        if self.average_doc_length <= 0 and self.total_docs_in_collection > 0 : # avgdl can be 0 if N is 0
             # Allow avg_doc_length to be 0 if there are no documents, otherwise should be positive.
            if self.total_docs_in_collection > 0:
                 raise ValueError("Indexer's 'avgdl' (average document length) must be positive if N > 0.")


        self.idf_scores: Dict[str, float] = {}
        for term, postings in self.inverted_index.items():
            document_frequency = len(postings)
            

            idf_numerator = self.total_docs_in_collection - document_frequency + 0.5
            idf_denominator = document_frequency + 0.5
            

            if idf_denominator <= 0: 
                 self.idf_scores[term] = 0.0
            else:

                value_for_log = (idf_numerator / idf_denominator) + 1.0
                if value_for_log <= 0 :
                    self.idf_scores[term] = 0.0 
                else:
                    self.idf_scores[term] = math.log(value_for_log)


    def search_bm25(self, query_string: str, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> List[Tuple[str, float]]:
        """
        Ranks documents against a query using the BM25 algorithm.

        Args:
            query_string (str): The query string. Terms are extracted by simple splitting.
                                It's recommended to preprocess this query (e.g., lowercase,
                                tokenize) consistently with how terms in the index were processed.
            k1 (float): BM25 parameter, controls term frequency saturation.
            b (float): BM25 parameter, controls document length normalization.

        Returns:
            List[Tuple[str, float]]: A list of (document_id, score) tuples,
                                     sorted in descending order of score. Returns an
                                     empty list if the query string is empty or no
                                     relevant documents are found.
        """
        if not query_string:
            return []


        processed_query_terms: Set[str] = set(query_string.lower().split())
        if not processed_query_terms or (len(processed_query_terms) == 1 and "" in processed_query_terms):
            return []


        document_scores: Dict[str, float] = {}

        for term in processed_query_terms:
            if not term:
                continue
            
            term_idf: float = self.idf_scores.get(term, 0.0)
            if term_idf == 0.0: 
                continue

            if term in self.inverted_index:
                for document_id, term_freq_in_doc in self.inverted_index[term]:
                    doc_length: int = self.document_lengths.get(document_id, 0)
                    
                    # Skip if doc_length is unknown or invalid, or avg_doc_length is zero (and N > 0)
                    if doc_length <= 0 or (self.average_doc_length <= 0 and self.total_docs_in_collection > 0) :
                        continue

                    numerator = term_freq_in_doc * (k1 + 1)
                    # Denominator part of the term weight
                    denominator = term_freq_in_doc + k1 * (
                        1 - b + b * (doc_length / self.average_doc_length)
                    )
                    
                    if denominator == 0: # Avoid division by zero, though unlikely with typical k1, b > 0
                        term_score_for_doc = 0.0
                    else:
                        term_score_for_doc = term_idf * (numerator / denominator)
                    
                    document_scores[document_id] = document_scores.get(document_id, 0.0) + term_score_for_doc
        
        return sorted(document_scores.items(), key=lambda item: item[1], reverse=True)

    def search_boolean_and(self, query_terms: List[str]) -> List[str]:
        """
        Performs a boolean AND search for documents containing all specified terms.
        Uses skip-pointer optimization for intersecting postings lists.
        Assumes terms in `query_terms` are pre-processed (e.g., lowercased)
        to match the terms in the inverted index.

        Args:
            query_terms (List[str]): A list of pre-processed terms to search for.

        Returns:
            List[str]: A list of document_ids that contain all query terms.
                       The list is typically sorted by document_id as a byproduct
                       of the intersection process if postings lists are sorted.
                       Returns an empty list if no query terms are provided, any term
                       is not found, or no documents satisfy the AND condition.
        """
        if not query_terms:
            return []


        processed_unique_terms = set(term.lower() for term in query_terms if term)
        if not processed_unique_terms:
            return []

        term_postings_lists: List[List[str]] = []
        for term in processed_unique_terms:
            if term in self.inverted_index:
                # Extract only document IDs. The original `self.idx[t]` was
                # `List[Tuple[doc, tf]]`.
                doc_ids_for_term = [doc_id for doc_id, _tf in self.inverted_index[term]]
                if not doc_ids_for_term: # Term in index, but empty postings list
                    return [] # AND condition cannot be met
                term_postings_lists.append(doc_ids_for_term)
            else:
                # If any term is not in the index, the AND result is empty.
                return []
        
        if not term_postings_lists: #  safeguard.
            return []

        # Sort lists by length to start intersection with the smallest list (optimization)
        term_postings_lists.sort(key=len)

        # Iteratively intersect the sorted lists
        result_list: List[str] = term_postings_lists[0]
        for i in range(1, len(term_postings_lists)):
            result_list = self._intersect_postings_with_skips(result_list, term_postings_lists[i])
            if not result_list: # If any intersection results in empty, final result is empty
                break 
        
        return result_list

    def _intersect_postings_with_skips(self, list1: List[str], list2: List[str]) -> List[str]:
        """
        Intersects two sorted lists of document IDs using skip pointers.
        Skip distance is approximately sqrt(length of list).
        Assumes document IDs are comparable (e.g., strings or integers).

        Args:
            list1 (List[str]): The first sorted list of document IDs.
            list2 (List[str]): The second sorted list of document IDs.

        Returns:
            List[str]: A new sorted list containing common document IDs.
        """
        intersection_result: List[str] = []
        idx1, idx2 = 0, 0
        len1, len2 = len(list1), len(list2)

        # Calculate skip distances; ensure it's at least 1.
        # The `or 1` was in the original code and is a good fallback for very short lists.
        skip_dist1 = int(math.sqrt(len1)) or 1
        skip_dist2 = int(math.sqrt(len2)) or 1

        while idx1 < len1 and idx2 < len2:
            doc_id1 = list1[idx1]
            doc_id2 = list2[idx2]

            if doc_id1 == doc_id2:
                intersection_result.append(doc_id1)
                idx1 += 1
                idx2 += 1
            elif doc_id1 < doc_id2:
                # Try to skip in list1
                potential_next_idx1 = idx1 + skip_dist1
                # Check if skip is valid and doesn't jump past the target (doc_id2)
                if potential_next_idx1 < len1 and list1[potential_next_idx1] <= doc_id2:
                    idx1 = potential_next_idx1
                else:
                    idx1 += 1 # Advance normally
            else: # doc_id2 < doc_id1
                # Try to skip in list2
                potential_next_idx2 = idx2 + skip_dist2
                # Check if skip is valid and doesn't jump past the target (doc_id1)
                if potential_next_idx2 < len2 and list2[potential_next_idx2] <= doc_id1:
                    idx2 = potential_next_idx2
                else:
                    idx2 += 1 # Advance normally
        
        return intersection_result