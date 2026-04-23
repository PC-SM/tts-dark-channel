from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import edge_tts
import base64
import tempfile
import os
import re
import httpx
import subprocess
import random
from typing import List, Optional

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
    drive_folder_id: str = ""
    usar_videos_pexels: bool = True
    ken_burns: bool = True
    transicoes: bool = True
    duracao_transicao: float = 0.8
    trilha_url: Optional[str] = None
    volume_trilha: float = 0.12
    overlay_titulo: bool = True
    watermark_text: str = "CANAL DARK"

def converter_pausas(texto: str) -> str:
    texto = re.sub(r'\[PAUSA_LONGA\]', '. . . . .', texto)
    texto = re.sub(r'\[PAUSA\]', '. . .', texto)
    return texto

def sanitizar_titulo(titulo: str) -> str:
    return re.sub(r"[^\w\s\-]", "", titulo)[:50].strip()

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

def get_duracao_ffprobe(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())
    except Exception:
        return 5.0

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
    return {"audio_base64": audio_b64, "formato": "mp3", "bloco_index": bloco_index, "titulo": titulo}

@app.post("/juntar")
async def juntar(req: JuntarRequest):
    combined_bytes = b""
    for b64 in req.blocos_base64:
        combined_bytes += base64.b64decode(b64)
    return {"audio_base64": base64.b64encode(combined_bytes).decode(), "titulo": req.titulo, "formato": "mp3"}

@app.post("/montar")
async def montar(req: VideoRequest):
    tmpdir = tempfile.mkdtemp()
    try:
        audio_path = os.path.join(tmpdir, "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(base64.b64decode(req.audio_base64))
        duracao_total = get_duracao(audio_path)

        media_paths = []
        W, H = "854", "480"

        if req.usar_videos_pexels:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for kw in req.palavras_chave[:4]:
                    if len(media_paths) >= 8:
                        break
                    try:
                        r = await client.get(
                            "https://api.pexels.com/videos/search",
                            headers={"Authorization": req.pexels_key},
                            params={"query": kw, "orientation": "landscape", "size": "medium", "per_page": 3}
                        )
                        for video in r.json().get("videos", []):
                            files = sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
                            hd = next((f for f in files if f.get("width", 0) <= 1920 and f.get("file_type") == "video/mp4"), files[0] if files else None)
                            if hd:
                                dest = os.path.join(tmpdir, f"clip_{len(media_paths):02d}.mp4")
                                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl:
                                    resp = await dl.get(hd["link"])
                                    with open(dest, "wb") as f:
                                        f.write(resp.content)
                                if os.path.getsize(dest) > 1000:
                                    media_paths.append(("video", dest))
                                if len(media_paths) >= 8:
                                    break
                    except Exception:
                        continue

        if len(media_paths) < 4:
            media_paths = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                imagens = []
                for kw in req.palavras_chave[:3]:
                    resp = await client.get(
                        "https://api.pexels.com/v1/search",
                        headers={"Authorization": req.pexels_key},
                        params={"query": kw, "per_page": 5, "orientation": "landscape"}
                    )
                    for photo in resp.json().get("photos", []):
                        imagens.append(photo["src"]["medium"])
                    if len(imagens) >= 9:
                        break
                for idx, url in enumerate(imagens[:9]):
                    try:
                        img_resp = await client.get(url)
                        img_path = os.path.join(tmpdir, f"img_{idx:03d}.jpg")
                        with open(img_path, "wb") as f:
                            f.write(img_resp.content)
                        media_paths.append(("foto", img_path))
                    except Exception:
                        continue

        if not media_paths:
            raise Exception("Nenhuma mídia encontrada")

        n = len(media_paths)
        dur_seg = max(3.0, min(duracao_total / n, 12.0))
        segmentos = []

        for idx, (tipo, path) in enumerate(media_paths):
            seg = os.path.join(tmpdir, f"seg_{idx:02d}.mp4")
            if tipo == "video":
                dur_usar = min(get_duracao_ffprobe(path), dur_seg + req.duracao_transicao)
                cmd = ["ffmpeg", "-y", "-i", path, "-t", str(dur_usar),
                       "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-r", "24", "-an", seg]
            else:
                if req.ken_burns:
                    z = "min(zoom+0.0015,1.3)" if random.choice([True, False]) else "if(lte(zoom,1.0),1.3,max(1.0,zoom-0.0015))"
                    vf = f"scale=4000:-1,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(dur_seg*24)}:s={W}x{H}:fps=24,format=yuv420p"
                else:
                    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
                cmd = ["ffmpeg", "-y", "-loop", "1", "-i", path, "-t", str(dur_seg),
                       "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-r", "24", "-an", seg]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0 and os.path.exists(seg):
                segmentos.append(seg)

        if not segmentos:
            raise Exception("Nenhum segmento gerado")

        slideshow_path = os.path.join(tmpdir, "slideshow.mp4")
        transicoes_ok = False

        if req.transicoes and len(segmentos) > 1:
            td = req.duracao_transicao
            duracoes = [get_duracao_ffprobe(s) for s in segmentos]
            input_args = []
            for s in segmentos:
                input_args += ["-i", s]
            filter_parts = []
            offset = 0.0
            label_in = "[0:v]"
            for i in range(1, len(segmentos)):
                offset += duracoes[i - 1] - td
                label_out = "[vout]" if i == len(segmentos) - 1 else f"[v{i}]"
                transition = random.choice(["fade", "dissolve", "wipeleft", "wiperight"])
                filter_parts.append(f"{label_in}[{i}:v]xfade=transition={transition}:duration={td}:offset={offset:.3f}{label_out}")
                label_in = f"[v{i}]"
            result = subprocess.run(
                ["ffmpeg", "-y", *input_args, "-filter_complex", ";".join(filter_parts),
                 "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-r", "24", slideshow_path],
                capture_output=True
            )
            transicoes_ok = result.returncode == 0 and os.path.exists(slideshow_path)

        if not transicoes_ok:
            list_path = os.path.join(tmpdir, "segments.txt")
            with open(list_path, "w") as f:
                for s in segmentos:
                    f.write(f"file '{s}'\n")
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-r", "24", slideshow_path],
                           capture_output=True)

        if not os.path.exists(slideshow_path) or os.path.getsize(slideshow_path) < 1000:
            raise Exception("Slideshow falhou")

        if req.overlay_titulo or req.watermark_text:
            overlaid_path = os.path.join(tmpdir, "overlaid.mp4")
            vf_parts = []
            if req.overlay_titulo:
                t = sanitizar_titulo(req.titulo)
                vf_parts.append(f"drawtext=text='{t}':fontsize=36:fontcolor=white:x=(w-text_w)/2:y=h*0.75:shadowcolor=black@0.8:shadowx=2:shadowy=2:enable='between(t,0.5,5.5)'")
            if req.watermark_text:
                wm = req.watermark_text.replace("'", "")
                vf_parts.append(f"drawtext=text='{wm}':fontsize=16:fontcolor=white@0.45:x=w-text_w-10:y=h-text_h-10:shadowcolor=black@0.5:shadowx=1:shadowy=1")
            result = subprocess.run(["ffmpeg", "-y", "-i", slideshow_path, "-vf", ",".join(vf_parts),
                                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", overlaid_path],
                                    capture_output=True)
            if result.returncode == 0 and os.path.exists(overlaid_path):
                slideshow_path = overlaid_path

        video_path = os.path.join(tmpdir, "video_final.mp4")
        subprocess.run(["ffmpeg", "-y", "-i", slideshow_path, "-i", audio_path,
                        "-c:v", "copy", "-c:a", "aac", "-shortest", video_path],
                       capture_output=True)

        if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
            raise Exception("Vídeo final falhou")

        with open(video_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode()

        nome = req.titulo.encode('ascii', 'ignore').decode().replace(' ', '_')[:60] + "_VIDEO.mp4"
        return {"video_base64": video_b64, "titulo": req.titulo, "nome_arquivo": nome, "formato": "mp4"}

    finally:
        for fname in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, fname))
            except Exception:
                pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass

@app.get("/legal", response_class=HTMLResponse)
def legal():
    return """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Legal</title></head>
    <body><h1>Terms of Service</h1><p>This application is used for automated video publishing.</p>
    <h1>Privacy Policy</h1><p>This application does not collect or store personal data from users.</p></body></html>"""

@app.get("/")
def health():
    return {"status": "ok", "versao": "fase-a"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
