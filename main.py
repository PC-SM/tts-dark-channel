from fastapi import FastAPI
from pydantic import BaseModel
import edge_tts
import asyncio
import base64
import tempfile
import os

app = FastAPI()

class TTSRequest(BaseModel):
    texto: str
    voz: str = "pt-BR-AntonioNeural"

@app.post("/narrar")
async def narrar(req: TTSRequest):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmpfile = f.name
    communicate = edge_tts.Communicate(req.texto, req.voz)
    await communicate.save(tmpfile)
    with open(tmpfile, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    os.unlink(tmpfile)
    return {"audio_base64": audio_b64, "formato": "mp3"}

@app.get("/")
def health():
    return {"status": "ok"}
