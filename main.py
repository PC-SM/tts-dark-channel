from fastapi import FastAPI, Query
from pydantic import BaseModel
import edge_tts
import base64
import tempfile
import os
import re
import httpx
import subprocess
import json
from typing import List
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

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
    drive_folder_id: str

def converter_pausas(texto: str) -> str:
    texto = re.sub(r'\[PAUSA_LONGA\]', '. . . . .', texto)
    texto = re.sub(r'\[PAUSA\]', '. . .', texto)
    return texto

def get_duracao(audio_path: str) -> float:
    result = subprocess.run(
        ["ffmpeg", "-i", audio_path, "-f", "null", "-"],
        capture_output=True, text=True
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        return 60.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)

def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def upload_to_drive(file_path: str, filename: str, folder_id: str) -> str:
    service = get_drive_service()
    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()
    return file.get("webViewLink")

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

    async with httpx.AsyncClient(timeout=30.0) as client:
        imagens = []
        for kw in req.palavras_chave[:3]:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": req.pexels_key},
                params={"query": kw, "per_page": 5, "orientation": "landscape"}
            )
            data = resp.json()
            for photo in data.get("photos", []):
                imagens.append(photo["src"]["medium"])
            if len(imagens) >= 9:
                break

    if not imagens:
        raise Exception("Nenhuma imagem encontrada na Pexels")

    img_paths = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for idx, url in enumerate(imagens[:9]):
            img_resp = await client.get(url)
            img_path = os.path.join(tmpdir, f"img_{idx:03d}.jpg")
            with open(img_path, "wb") as f:
                f.write(img_resp.content)
            img_paths.append(img_path)

    duracao = get_duracao(audio_path)
    tempo_por_imagem = duracao / len(img_paths)

    list_path = os.path.join(tmpdir, "images.txt")
    with open(list_path, "w") as f:
        for img_path in img_paths:
            f.write(f"file '{img_path}'\n")
            f.write(f"duration {tempo_por_imagem:.2f}\n")
        f.write(f"file '{img_paths[-1]}'\n")
        f.write(f"duration 1\n")

    slideshow_path = os.path.join(tmpdir, "slideshow.mp4")
    result1 = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-r", "25",
        "-pix_fmt", "yuv420p",
        slideshow_path
    ], capture_output=True, text=True)

    if not os.path.exists(slideshow_path) or os.path.getsize(slideshow_path) < 1000:
        raise Exception(f"Slideshow falhou: {result1.stderr[-3000:]}")

    video_path = os.path.join(tmpdir, "video_final.mp4")
    result2 = subprocess.run([
        "ffmpeg", "-y",
        "-i", slideshow_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        video_path
    ], capture_output=True, text=True)

    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
        raise Exception(f"Video final falhou: {result2.stderr[-3000:]}")

    nome_arquivo = req.titulo.encode('ascii', 'ignore').decode().replace(' ', '_')[:60] + "_VIDEO.mp4"
    link = upload_to_drive(video_path, nome_arquivo, req.drive_folder_id)

    for arq in os.listdir(tmpdir):
        try:
            os.remove(os.path.join(tmpdir, arq))
        except:
            pass
    os.rmdir(tmpdir)

    return {
        "drive_link": link,
        "titulo": req.titulo,
        "nome_arquivo": nome_arquivo
    }

@app.get("/")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
