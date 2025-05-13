import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI



# init
load_dotenv()
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

# summarization prompt
summarize_template = """
You are an assistant that turns a list of email snippets into a concise daily digest.

Emails:
{emails}

Write a clear, bullet‑point summary of the key points.
"""
prompt = ChatPromptTemplate.from_template(summarize_template)
summarizer = prompt | llm | StrOutputParser()

def summarize_emails(snippets: list[str]) -> str:
    """
    snippets: list of strings like "Subject: … – snippet of body"
    returns: a bullet‑point summary
    """
    payload = {"emails": "\n".join(f"- {s}" for s in snippets)}
    return summarizer.invoke(payload)

if __name__ == "__main__":
    # sample test case
    sample = [
        "Meeting tomorrow at 10am – please confirm your availability.",
        "Your invoice is ready – see attached PDF.",
    ]
    print("Digest:\n", summarize_emails(sample))
