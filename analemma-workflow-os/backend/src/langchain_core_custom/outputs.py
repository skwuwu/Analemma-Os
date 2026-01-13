from typing import List, Any, Optional
from pydantic import BaseModel

class Generation(BaseModel):
    text: str
    message: Optional[Any] = None
    generation_info: Optional[Any] = None

class LLMResult(BaseModel):
    generations: List[List[Generation]]
    llm_output: Optional[dict] = None
    run: Optional[Any] = None
