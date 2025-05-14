import heapq
from typing import Dict, List, Tuple

class TrieNode:
    """
    A node in the Trie data structure.

    Attributes:
        children (Dict[str, TrieNode]): A dictionary mapping characters to child TrieNodes.
        is_end_of_word (bool): True if this node marks the end of a complete word.
        frequency (int): The frequency of the word ending at this node. If 0, it means
                         this node is part of a path to a word but not an end itself,
                         or the word has not been assigned a frequency.
        top_k_suggestions (List[Tuple[int, str]]): A min-heap storing the top k words
                                                 (by frequency) that pass through or
                                                 end at this node. Stores tuples of
                                                 (-frequency, word) to simulate a max-heap.
    """
    __slots__ = ('children', 'is_end_of_word', 'frequency', 'top_k_suggestions')

    def __init__(self) -> None:
        """Initializes a new TrieNode."""
        self.children: Dict[str, TrieNode] = {}
        self.is_end_of_word: bool = False
        self.frequency: int = 0
        # Stores (-frequency, word) to use min-heap as a max-heap for top k
        self.top_k_suggestions: List[Tuple[int, str]] = []

class AutocompleteSystem:
    """
    An autocomplete system using a Trie to store words and their frequencies,
    providing suggestions based on a given prefix.
    """

    def __init__(self, k: int = 5):
        """
        Initializes the AutocompleteSystem.

        Args:
            k (int): The number of top suggestions to return. Defaults to 5.
        """
        if k <= 0:
            raise ValueError("k must be a positive integer.")
        self.root: TrieNode = TrieNode()
        self.k_suggestions: int = k

    def insert(self, word: str, frequency: int) -> None:
        """
        Inserts a word with its frequency into the Trie.

        If the word already exists, its frequency is updated. The top_k_suggestions
        list for each node along the path of the word is updated.

        Args:
            word (str): The word to insert.
            frequency (int): The frequency of the word.
        """
        if not isinstance(word, str) or not word:
            print("Warning: Word must be a non-empty string.")
            return
        if not isinstance(frequency, int) or frequency < 0:
            print("Warning: Frequency must be a non-negative integer.")
            return


        node: TrieNode = self.root
        for char_index, char_code in enumerate(word):
            character = chr(char_code) if isinstance(char_code, int) else char_code # handle both str and List[int]
            if character not in node.children:
                node.children[character] = TrieNode()
            node = node.children[character]
            self._update_top_k(node, (frequency, word))

        node.is_end_of_word = True
        node.frequency = frequency # Update frequency if word already existed

    def _update_top_k(self, node: TrieNode, word_freq_pair: Tuple[int, str]) -> None:
        """
        Updates the top_k_suggestions list for a given TrieNode.

        Maintains a min-heap of size at most self.k_suggestions, storing
        (-frequency, word) tuples to simulate a max-heap behavior based on frequency.

        Args:
            node (TrieNode): The TrieNode whose top_k_suggestions list is to be updated.
            word_freq_pair (Tuple[int, str]): A tuple containing (frequency, word).
        """
        freq, word = word_freq_pair
        # Use negative frequency for min-heap to act as max-heap
        heap_item = (-freq, word)

        found_and_updated = False
        for i, (current_neg_freq, current_word) in enumerate(node.top_k_suggestions):
            if current_word == word:
                if -current_neg_freq < freq : 
                    node.top_k_suggestions[i] = heap_item
                    heapq.heapify(node.top_k_suggestions) 
                found_and_updated = True
                break
        
        if not found_and_updated:
            heapq.heappush(node.top_k_suggestions, heap_item)

        while len(node.top_k_suggestions) > self.k_suggestions:
            heapq.heappop(node.top_k_suggestions)


    def suggest(self, prefix: str) -> List[str]:
        """
        Suggests the top k words starting with the given prefix.

        Args:
            prefix (str): The prefix to search for.

        Returns:
            List[str]: A list of the top k suggested words, sorted by frequency
                       in descending order. Returns an empty list if the prefix
                       is not found or no words match.
        """
        if not isinstance(prefix, str):
            return []

        node: TrieNode = self.root
        for char_code in prefix:
            character = chr(char_code) if isinstance(char_code, int) else char_code # handle both str and List[int]
            if character not in node.children:
                return []  # Prefix not found in Trie
            node = node.children[character]

        # The top_k_suggestions are stored as (-freq, word)
        # Sort them by frequency (descending) and extract the words
        # Sorting is necessary because heapq only guarantees the smallest item is at index 0
        # reverse=True ->  smallest negative is largest positive.
        sorted_suggestions = sorted(node.top_k_suggestions, key=lambda x: x[0])
        return [word for neg_freq, word in sorted_suggestions]