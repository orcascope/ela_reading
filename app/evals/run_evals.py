import asyncio,json, time
from pathlib import Path
from app.evals.checks import valid_output

CONCURRENCY = 4

HERE = Path(__file__).resolve().parent
GOLDEN = HERE/"golden.jsonl"

from app.llm import llm_client, explain_selection

def load_golden():
    rows = []
    for n, line in enumerate(GOLDEN.read_text(encoding="utf-8").splitlines()):
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Decode error in line {n}")
    return rows


async def run_eval_target(client, eval_row):
    async for chunk in explain_selection(client, eval_row["selected_text"],
                                         eval_row["context"]):
        final = chunk
    return json.loads(final)

async def run_row(sem, client, eval_row):
    """ function will run the eval json into the actual llm function to be tested 
    with - Collect the output and write to RESULTS/ folder.
    Also run the checks and semantic similiarity and provide a score for row.
    """
    async with sem:
        t0 = time.perf_counter()
        model_output = await run_eval_target(client, eval_row)
        latency = time.perf_counter() -t0
    try:
        t1 = valid_output(eval_row, model_output)
    except Exception as e:
        raise Exception
    eval_output = model_output | t1 | {"latency":latency}
    return eval_output

def summarize(records):
    return {"pass":2, "fail": 1}

async def main():
    rows = load_golden()
    print(rows)
    client = llm_client()
    sem = asyncio.Semaphore(CONCURRENCY)
    records = await asyncio.gather(*(run_row(sem, client, r) for r in rows))
    print(records)
    summary = summarize(records)
    # TODO: print a readable table, then print the path returned by save(...)


if __name__ == "__main__":
    asyncio.run(main())