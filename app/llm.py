"""Live "explain this word/sentence" help while a student is reading.

Called from server.py's /api/explain route. One Claude call returns both the
plain-language meaning and a note on how the selection fits the surrounding
passage. Nothing here is persisted - saving to the vocabulary list is a
separate, explicit action (see /api/vocabulary in server.py).
"""
import asyncio

from dotenv import load_dotenv
load_dotenv()
from collections.abc import AsyncIterator
import json, logging, os

from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, ValidationError
from langchain.chat_models import init_chat_model

MODEL = "system.ai.meta-llama-3-3-70b-instruct"
DATABRICKS_TOKEN = os.getenv("LLM_ACCESS_KEY")

log = logging.getLogger(__name__)

def get_client():
    from langchain_openai import ChatOpenAI
    MODEL = "system.ai.meta-llama-3-3-70b-instruct"
    DATABRICKS_TOKEN = os.getenv("LLM_ACCESS_KEY")
    api_key=DATABRICKS_TOKEN
    base_url="https://dbc-d7d09c06-4d54.cloud.databricks.com/ai-gateway/mlflow/v1"
    model=MODEL
    extra_args = {
        "extra_body": {"max_tokens": 300},
        "max_retries":2,
        "timeout": 20
        }
    #     )
    provider = os.getenv("LLM_PROVIDER")
    return build_client(provider, base_url, model, extra_args  )

def build_client(provider, base_url, model, kwargs):
    if provider == 'databricks':
        print("init_chat")
        print(model)
        return ChatOpenAI(model=model, base_url=base_url, api_key=DATABRICKS_TOKEN, **kwargs)
    if provider == 'claude':
        return init_chat_model("claude:sonnet-5.5")


class TextExplanation(BaseModel):
    """The model's answer: what the selection means, and how it fits the passage."""
    meaning: str
    fit: str

class ExplainError(RuntimeError):
    """Raised when the explanation could not be produced."""



PROMPT_TEMPLATE = """A Grade 10 student is reading a fiction in english text they don't understand.

Selected text: {selected_text!r}

The passage it appears in:
\"\"\"
{context}
\"\"\"

Reply in JSON format with MEANING and FIT as :
{{"meaning": "..." , "fit": "..." }}

- meaning: 1-2 sentences, plain Grade-10-level language, giving the actual \
meaning of the selected word or phrase as it is used here (not a dictionary \
entry with every sense - just the sense that fits).
- fit: 1-2 sentences explaining how that word or sentence functions \
in, or connects to, the surrounding passage.
"""


async def explain_selection(client, selected_text: str, context: str) -> AsyncIterator[str]:
    """ yields text pieces"""
    prompt = PROMPT_TEMPLATE.format(selected_text=selected_text, context=context)
    
    # cliet_struct_op = client.with_structured_output(TextExplanation) 

    chain = client | JsonOutputParser(pydantic_object=TextExplanation)
    response = chain.astream(
        [{"role": "user", "content": prompt}],
    )

    try:
        last = {}
        async with asyncio.timeout(20):
            async for chunk in response:
                last = chunk
                yield json.dumps(last) + "\n"
        TextExplanation.model_validate_json(json.dumps(last))
    except Exception as exc:
            yield json.dumps({"error": f"LLM API request failed: {exc}"})
            


def make_sse_packet(event_name, data):
    return f"event:{event_name}\ndata:{json.dumps(data)}\n\n"

async def explain_selection_sse(client, selected_text: str, context: str) -> AsyncIterator[str]:
    """ yields text pieces"""
    prompt = PROMPT_TEMPLATE.format(selected_text=selected_text, context=context)
    
    # cliet_struct_op = client.with_structured_output(TextExplanation) 

    chain = client | JsonOutputParser(pydantic_object=TextExplanation)
    response = chain.astream(
        [{"role": "user", "content": prompt}],
    )

    try:
        last = {}
        async with asyncio.timeout(20):
            async for chunk in response:
                last = chunk
                yield make_sse_packet("partial", last)
            TextExplanation.model_validate(last)
            yield make_sse_packet("done", last)
    except Exception as exc:
            log.exception("error in sse")
            yield make_sse_packet("error", {"message": "api_request_failed"})
            


async def run():
    async for chunk in explain_selection(get_client(), "bus", "wheels on the bus go round"):
        print(chunk)


if __name__ == "__main__":
    asyncio.run(run())     
