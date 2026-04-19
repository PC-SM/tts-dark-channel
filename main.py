from fastapi import FastAPI, Query
from pydantic import BaseModel
import edge_tts
import asyncio
import base64
import tempfile
import os
import re
from typing import List

app = FastAPI()

class TTSRequest(BaseModel):
    texto: str
    voz: str = "pt-BR-AntonioNeural"

class JuntarRequest(BaseModel):
    blocos_base64: List[str]
    titulo: str = "audio_final"

def converter_pausas(texto: str) -> str:
    texto = re.sub(r'\[PAUSA_LONGA\]', '. . . . .', texto)
    texto = re.sub(r'\[PAUSA\]', '. . .', texto)
    return texto

@app.post("/narrar")
async def narrar(
    req: TTSRequest,
    bloco_index: int = Query(0),
    titulo: str = Query("")
):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmpfile = f.name

    texto_limpo = converter_pausas(req.texto)
    communicate = edge_tts.Communicate(texto_limpo, req.voz)
    await communicate.save(tmpfile)

    with open(tmpfile, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    os.unlink(tmpfile)

    return {
        "audio_base64": audio_b64,
        "formato": "mp3",
        "bloco_index": bloco_index,
        "titulo": titulo
    }

@app.post("/juntar")
async def juntar(req: JuntarRequest):
    combined_bytes = b""
    for b64 in req.blocos_base64:
        combined_bytes += base64.b64decode(b64)

    audio_final_b64 = base64.b64encode(combined_bytes).decode()

    return {
        "audio_base64": audio_final_b64,
        "titulo": req.titulo,
        "formato": "mp3"
    }

@app.get("/")
def health():
    return {"status": "ok"}
