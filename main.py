from fastapi import FastAPI
from pydantic import BaseModel
import edge_tts
import asyncio
import base64
import tempfile
import os
import re

app = FastAPI()

class TTSRequest(BaseModel):
    texto: str
    voz: str = "pt-BR-AntonioNeural"

def converter_pausas(texto: str) -> str:
    texto = re.sub(r'\[PAUSA_LONGA\]', '<break time="2s"/>', texto)
    texto = re.sub(r'\[PAUSA\]', '<break time="1s"/>', texto)
    texto = re.sub(r'\.\.\.', '<break time="800ms"/>', texto)
    ssml = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
        xml:lang="pt-BR">
        <voice name="{'{}'}">{texto}</voice>
    </speak>"""
    return ssml

@app.post("/narrar")
async def narrar(req: TTSRequest):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmpfile = f.name
    
    ssml = converter_pausas(req.texto).format(req.voz)
    
    communicate = edge_tts.Communicate(ssml, req.voz)
    await communicate.save(tmpfile)
    
    with open(tmpfile, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    os.unlink(tmpfile)
    return {"audio_base64": audio_b64, "formato": "mp3"}

@app.get("/")
def health():
    return {"status": "ok"}
