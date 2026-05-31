from pydantic import BaseModel, Field
from typing import Optional


class EvalRequest(BaseModel):
    input_text: str = Field(..., description="Prompt o entrada original")
    output_text: str = Field(..., description="Respuesta generada por el modelo")

    tenant_id: Optional[str] = Field(None, description="Cliente")
    app_id: Optional[str] = Field(None, description="Aplicación")
    model_name: Optional[str] = Field(None, description="Modelo evaluado")
    prompt_version: Optional[str] = Field(None, description="Versión del prompt")


class EvalResponse(BaseModel):
    relevance: int
    clarity: int
    correctness: int
    overall: int
    recommendation: str