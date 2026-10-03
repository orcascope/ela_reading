import asyncio
import httpx, selectors, pytest
from app.server import app, get_db
from httpx import AsyncClient
from asgi_lifespan import LifespanManager
from app.server import get_openai_client

class MockLLMConnection:
    def __init__(self):
        pass

    async def astream(self, messages): yield "FakeChunk" 

class MockDBConnection:
    def __init__(self):
        pass

    async def execute(self, query): return {"FakeData":1}     

# @pytest.mark.asyncio
async def test_app():
    transport = httpx.ASGITransport(app=app)

    app.dependency_overrides[get_openai_client] = MockLLMConnection
    app.dependency_overrides[get_db] = MockDBConnection

    # async with LifespanManager(app=app) as manager:
    async with AsyncClient(transport=transport, 
                        base_url="http://localhost:8010"
                        ) as http_client:
        response = await http_client.get("/api/test_route")
        assert response.status_code == 200

        r = await http_client.post("/api/explain", json={
                "student": 1, "lesson_id": "x", "selected_text": "reluctant", "context": "..."})
        assert r.status_code == 200
        assert r.text == "MEANING: test FIT: ok"
    # print(response)
    # return response 

# asyncio.run(my_test()
#             loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))