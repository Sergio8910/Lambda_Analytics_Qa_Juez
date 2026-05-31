from fastapi import FastAPI, HTTPException
from juez.schemas import EvalRequest, EvalResponse
from juez.judge import run_judge
from juez.settings import settings

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="AI Evaluation Service powered by LangWatch"
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "environment": settings.ENV
    }


@app.post("/evaluate", response_model=EvalResponse)
def evaluate(request: EvalRequest):

    try:
        result = run_judge(
            input_text=request.input_text,
            output_text=request.output_text
        )

        return EvalResponse(**result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))