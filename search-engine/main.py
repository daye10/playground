import os
import sys # For exiting gracefully
from dotenv import load_dotenv


from indexer import Indexer
from search import SearchEngine
from autocomplete import AutocompleteSystem 

DEFAULT_SEARCH_RESULTS_LIMIT = 10

def print_help():
    """Prints the available commands and their descriptions."""
    print("\nTextSearch++ ready. Commands:")
    print("  search <query>         — Perform a BM25-ranked search for the query.")
    print("  and <term1> <term2>... — Find documents containing all specified terms (boolean AND).")
    print("  suggest <prefix>       — Get autocomplete suggestions for a prefix.")
    print("  help                   — Show this help message.")
    print("  quit                   — Exit the application.")
    print("-" * 30)

def run_application():
    load_dotenv()
    print("Initializing TextSearch++...")
    
    indexer = Indexer()
    try:
        indexer.build_index() 
    except FileNotFoundError as e:
        print(f"Error: Indexing directory not found. {e}", file=sys.stderr)
        print("Please ensure the TEXT_DIR environment variable is set correctly or the './texts' directory exists.", file=sys.stderr)
        sys.exit(1)
    except Exception as e: 
        print(f"An unexpected error occurred during index building: {e}", file=sys.stderr)
        sys.exit(1)

    if indexer.N == 0:
        print("Warning: The index is empty. Search and suggest features may not work as expected.", file=sys.stderr)

    # Prepare search engine & autocomplete system
    search_engine = SearchEngine(indexer)
    autocomplete_system = AutocompleteSystem(k=5) # Default k, or make it configurable

    # Populate the autocomplete system.
    # Using document frequency (number of documents a term appears in) as the
    # "frequency" score for autocomplete suggestions.
    print("Populating autocomplete suggestions...")
    if hasattr(indexer, 'inverted_index') and indexer.inverted_index:
        for term, postings_list in indexer.inverted_index.items():
            # `len(postings_list)` is the document frequency of the term.
            autocomplete_system.insert(term, len(postings_list))
    else:
        print("Warning: Inverted index is empty or not available; autocomplete might not have suggestions.")

    print_help()

    # llop to process user commands
    while True:
        try:
            user_input = input(">> ").strip()
            if not user_input:
                continue

            command_parts = user_input.split()
            command = command_parts[0].lower() # Case-insensitive command

            if command == "quit":
                print("Exiting TextSearch++.")
                break
            elif command == "help":
                print_help()
            elif command == "search":
                if len(command_parts) < 2:
                    print("Usage: search <query>")
                    continue
                query = " ".join(command_parts[1:])
                results = search_engine.search_bm25(query) 
                if results:
                    print(f"\nTop {min(len(results), DEFAULT_SEARCH_RESULTS_LIMIT)} results for '{query}':")
                    for doc_id, score in results[:DEFAULT_SEARCH_RESULTS_LIMIT]:
                        print(f"  {doc_id} (Score: {score:.2f})")
                else:
                    print(f"No results found for '{query}'.")
            elif command == "and":
                if len(command_parts) < 2:
                    print("Usage: and <term1> <term2>...")
                    continue
                terms_for_and_search = command_parts[1:]
                matching_docs = search_engine.search_boolean_and(terms_for_and_search)
                if matching_docs:
                    print("Documents matching all terms: " + " & ".join(matching_docs))
                else:
                    print("No documents match all specified terms.")
            elif command == "suggest":
                if len(command_parts) < 2:
                    print("Usage: suggest <prefix>")
                    continue
                prefix = command_parts[1]
                suggestions = autocomplete_system.suggest(prefix)
                if suggestions:
                    print("Suggestions: " + ", ".join(suggestions))
                else:
                    print(f"No suggestions found for prefix '{prefix}'.")
            else:
                print(f"Unknown command: '{command}'. Type 'help' for a list of commands.")
        
        except KeyboardInterrupt:  # Ctrl+C
            print("\nExiting TextSearch++ (Keyboard Interrupt).")
            break
        except EOFError: # Ctrl+D
            print("\nExiting TextSearch++ (EOF).")
            break
        except Exception as e: 
            print(f"An unexpected error occurred: {e}", file=sys.stderr)
            

if __name__ == "__main__":
    run_application() 