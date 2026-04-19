from fastapi import FastAPI, Query
from pydantic import BaseModel
import edge_tts
import asyncio
import base64
import tempfile
import os
import re
import httpx
import subprocess
from typing import List

app = FastAPI()

class TTSRequest(BaseModel):
    texto: str
    voz: str = "pt-BR-AntonioNeural"

class JuntarRequest(BaseModel):
    blocos_base64: List[str]
    titulo: str = "audio_final"

class VideoRequest(BaseModel):
    audio_base64: str
    palavras_chave: List[str]
    titulo: str
    pexels_key: str

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

@app.post("/montar")
async def montar(req: VideoRequest):
    tmpdir = tempfile.mkdtemp()

    audio_path = os.path.join(tmpdir, "narration.mp3")
    with open(audio_path, "wb") as f:
        f.write(base64.b64decode(req.audio_base64))

    async with httpx.AsyncClient() as client:
        imagens = []
        for kw in req.palavras_chave[:3]:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": req.pexels_key},
                params={"query": kw, "per_page": 5, "orientation": "landscape"}
            )
            data = resp.json()
            for photo in data.get("photos", []):
                imagens.append(photo["src"]["large"])
            if len(imagens) >= 10:
                break

    img_paths = []
    async with httpx.AsyncClient() as client:
        for idx, url in enumerate(imagens[:10]):
            img_resp = await client.get(url)
            img_path = os.path.join(tmpdir, f"img_{idx}.jpg")
            with open(img_path, "wb") as f:
                f.write(img_resp.content)
            img_paths.append(img_path)

   result = subprocess.run(
    ["ffmpeg", "-i", audio_path, "-f", "null", "-"],
    capture_output=True, text=True
    )
    import re as re2
    match = re2.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    h, m, s = match.groups()
    duracao = int(h) * 3600 + int(m) * 60 + float(s)
        tempo_por_imagem = duracao / len(img_paths)

    list_path = os.path.join(tmpdir, "images.txt")
    with open(list_path, "w") as f:
        for img_path in img_paths:
            f.write(f"file '{img_path}'\n")
            f.write(f"duration {tempo_por_imagem}\n")
        f.write(f"file '{img_paths[-1]}'\n")

    slideshow_path = os.path.join(tmpdir, "slideshow.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-r", "24", slideshow_path
    ], capture_output=True)

    video_path = os.path.join(tmpdir, "video_final.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", slideshow_path,
        "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac",
        "-shortest", video_path
    ], capture_output=True)

    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode()

    for arq in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, arq))
    os.rmdir(tmpdir)

    return {
        "video_base64": video_b64,
        "titulo": req.titulo,
        "formato": "mp4"
    }

@app.get("/")
def health():
    return {"status": "ok"}
