from fastapi import FastAPI
from pydantic import BaseModel

from app.agents.test_agent import TestAgent
from app.agents.models import LLMRouter

app = FastAPI(
    title="AI Test Agent",
    description="AI based test case generation service",
    version="1.0.0"
)


class TestRequest(BaseModel):
    model: str
    requirement: str


@app.post("/test-agent")
def run_test_agent(req: TestRequest):
    llm = LLMRouter(req.model)
    agent = TestAgent(llm)

    result = agent.generate_test_cases(req.requirement)
    return {
        "model": req.model,
        "result": result
    }


@app.get("/")
def health_check():
    return {"status": "ok"}
