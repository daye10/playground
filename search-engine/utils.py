import re

def tokenize(text: str) -> list[str]:
    """
    Lowercase, strip non-alphanumerics, split into words.
    """
    text = text.lower()
    # keep only words
    return re.findall(r'\b\w+\b', text)
