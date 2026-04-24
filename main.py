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
import json
from typing import List, Optional

app = FastAPI()

# ──────────────────────────────────────────────
# Tradução PT → EN para Pexels
# ──────────────────────────────────────────────

TRADUCOES = {
    "crime": "crime", "mistério": "mystery", "misterio": "mystery",
    "assassinato": "murder", "morte": "death", "terror": "horror",
    "assombrado": "haunted", "fantasma": "ghost", "sombrio": "dark",
    "escuro": "darkness", "noite": "night", "floresta": "forest",
    "abandono": "abandoned", "ruína": "ruins", "ruinas": "ruins",
    "sangue": "blood", "violência": "violence", "violencia": "violence",
    "serial": "serial killer", "sequestro": "kidnapping",
    "desaparecimento": "missing person", "investigação": "investigation",
    "investigacao": "investigation", "policia": "police", "polícia": "police",
    "brasil": "brazil", "cidade": "city", "urbano": "urban",
    "hospital": "hospital", "prisão": "prison", "prisao": "prison",
    "cemitério": "cemetery", "cemiterio": "cemetery", "igreja": "church",
    "chuva": "rain", "tempestade": "storm", "névoa": "fog", "nevoa": "fog",
    "escuridão": "darkness", "escuridao": "darkness", "sombra": "shadow",
    "medo": "fear", "pânico": "panic", "panico": "panic",
    "conspiração": "conspiracy", "conspiracao": "conspiracy",
    "governo": "government", "segredo": "secret", "mentira": "lie",
    "traição": "betrayal", "traicao": "betrayal", "vingança": "revenge",
    "vinganca": "revenge", "guerra": "war", "conflito": "conflict",
    "acidente": "accident", "tragédia": "tragedy", "tragedia": "tragedy",
    "desastre": "disaster", "fogo": "fire", "incêndio": "fire",
    "agua": "water", "mar": "sea", "oceano": "ocean",
    "tecnologia": "technology", "hacker": "hacker", "virus": "virus",
    "pandemia": "pandemic", "doença": "disease", "doenca": "disease",
    "veneno": "poison", "droga": "drugs", "tráfico": "trafficking",
    "trafico": "trafficking", "corrupção": "corruption", "corrupcao": "corruption",
    "criança": "child", "crianca": "child", "família": "family", "familia": "family",
    "cuba": "cuba", "eua": "usa", "político": "politics", "politico": "politics",
}

def traduzir_palavras(palavras: List[str]) -> List[str]:
    resultado = []
    for p in palavras:
        p_lower = p.lower().strip()
        resultado.append(TRADUCOES.get(p_lower, p_lower))
    return resultado

# ──────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────

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
    ken_burns: bool = False
    transicoes: bool = False
    duracao_transicao: float = 0.8
    trilha_url: Optional[str] = None
    volume_trilha: float = 0.12
    overlay_titulo: bool = True
    watermark_text: str = "CANAL DARK"

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def converter_pausas(texto: str) -> str:
    texto = re.sub(r'\[PAUSA_LONGA\]', '. . . . .', texto)
    texto = re.sub(r'\[PAUSA\]', '. . .', texto)
    return texto

def sanitizar_titulo(titulo: str) -> str:
    return re.sub(r"[^\w\s\-]", "", titulo)[:40].strip()

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

# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

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

    # Resolução Full HD para YouTube
    W, H = "1280", "720"

    try:
        # ── 1. Salvar narração ───────────────────────────────────────
        audio_path = os.path.join(tmpdir, "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(base64.b64decode(req.audio_base64))

        duracao_total = get_duracao(audio_path)

        # ── 2. Traduzir palavras-chave ───────────────────────────────
        palavras_en = traduzir_palavras(req.palavras_chave)
        print(f"[PEXELS] Palavras traduzidas: {palavras_en}")

        # ── 3. Buscar mídia visual ───────────────────────────────────
        media_paths = []

        if req.usar_videos_pexels:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for kw in palavras_en[:6]:  # Busca em mais palavras para ter mais opções
                    if len(media_paths) >= 10:
                        break
                    try:
                        r = await client.get(
                            "https://api.pexels.com/videos/search",
                            headers={"Authorization": req.pexels_key},
                            params={
                                "query": kw,
                                "orientation": "landscape",
                                "size": "medium",
                                "per_page": 5  # Mais resultados por busca
                            }
                        )
                        videos = r.json().get("videos", [])
                        print(f"[PEXELS] '{kw}': {len(videos)} vídeos encontrados")

                        for video in videos:
                            files = sorted(
                                video.get("video_files", []),
                                key=lambda x: x.get("width", 0),
                                reverse=True
                            )
                            # Prioriza HD (720p ou 1080p)
                            hd = next(
                                (f for f in files
                                 if 720 <= f.get("width", 0) <= 1920
                                 and f.get("file_type") == "video/mp4"),
                                next(
                                    (f for f in files if f.get("file_type") == "video/mp4"),
                                    files[0] if files else None
                                )
                            )
                            if hd:
                                dest = os.path.join(tmpdir, f"clip_{len(media_paths):02d}.mp4")
                                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as dl:
                                    resp = await dl.get(hd["link"])
                                    with open(dest, "wb") as f:
                                        f.write(resp.content)
                                if os.path.getsize(dest) > 10000:
                                    media_paths.append(("video", dest))
                                    print(f"[PEXELS] Clip baixado: {dest} ({os.path.getsize(dest)} bytes)")
                                if len(media_paths) >= 10:
                                    break
                    except Exception as e:
                        print(f"[PEXELS] Erro em '{kw}': {e}")
                        continue

        # Fallback para fotos se vídeos insuficientes
        if len(media_paths) < 4:
            print(f"[PEXELS] Poucos vídeos ({len(media_paths)}), buscando fotos...")
            media_paths = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                imagens = []
                for kw in palavras_en[:4]:
                    resp = await client.get(
                        "https://api.pexels.com/v1/search",
                        headers={"Authorization": req.pexels_key},
                        params={"query": kw, "per_page": 5, "orientation": "landscape",
                                "size": "large"}  # Fotos de alta qualidade
                    )
                    for photo in resp.json().get("photos", []):
                        # Usa versão large2x para melhor qualidade
                        imagens.append(photo["src"].get("large2x", photo["src"]["large"]))
                    if len(imagens) >= 12:
                        break

                for idx, url in enumerate(imagens[:12]):
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

        print(f"[MONTAR] Total de mídias: {len(media_paths)}")

        # ── 4. Normalizar cada mídia → segmento MP4 ─────────────────
        n = len(media_paths)
        dur_seg = max(4.0, min(duracao_total / n, 12.0))

        segmentos = []
        for idx, (tipo, path) in enumerate(media_paths):
            seg = os.path.join(tmpdir, f"seg_{idx:02d}.mp4")

            if tipo == "video":
                dur_clip = get_duracao_ffprobe(path)
                dur_usar = min(dur_clip, dur_seg + 1.0)
                cmd = [
                    "ffmpeg", "-y", "-i", path,
                    "-t", str(dur_usar),
                    "-vf", (
                        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                        f"format=yuv420p"
                    ),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-r", "30", "-an", seg
                ]
            else:
                # Foto: sem Ken Burns por padrão, mas escala para HD
                if req.ken_burns:
                    z = ("min(zoom+0.0015,1.3)" if random.choice([True, False])
                         else "if(lte(zoom,1.0),1.3,max(1.0,zoom-0.0015))")
                    vf = (
                        f"scale=4000:-1,"
                        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)'"
                        f":y='ih/2-(ih/zoom/2)':d={int(dur_seg*30)}:s={W}x{H}:fps=30,"
                        f"format=yuv420p"
                    )
                else:
                    vf = (
                        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                        f"format=yuv420p"
                    )
                cmd = [
                    "ffmpeg", "-y", "-loop", "1", "-i", path,
                    "-t", str(dur_seg), "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-r", "30", "-an", seg
                ]

            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                print(f"[FFMPEG] Erro no seg_{idx:02d}: {result.stderr.decode()[-500:]}")

            if result.returncode == 0 and os.path.exists(seg) and os.path.getsize(seg) > 1000:
                segmentos.append(seg)
            else:
                print(f"[FFMPEG] Segmento {idx} falhou ou vazio, pulando.")

        if not segmentos:
            raise Exception("Nenhum segmento gerado")

        print(f"[MONTAR] Segmentos gerados: {len(segmentos)}")

        # ── 5. Concatenar segmentos ──────────────────────────────────
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
                filter_parts.append(
                    f"{label_in}[{i}:v]xfade=transition={transition}:"
                    f"duration={td}:offset={offset:.3f}{label_out}"
                )
                label_in = f"[v{i}]"

            result = subprocess.run(
                ["ffmpeg", "-y", *input_args,
                 "-filter_complex", ";".join(filter_parts),
                 "-map", "[vout]",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
                 slideshow_path],
                capture_output=True
            )
            transicoes_ok = (result.returncode == 0
                             and os.path.exists(slideshow_path)
                             and os.path.getsize(slideshow_path) > 1000)

        if not transicoes_ok:
            list_path = os.path.join(tmpdir, "segments.txt")
            with open(list_path, "w") as f:
                for s in segmentos:
                    f.write(f"file '{s}'\n")

            result = subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
                slideshow_path
            ], capture_output=True)

            if result.returncode != 0:
                print(f"[FFMPEG] Erro concat: {result.stderr.decode()[-500:]}")

        if not os.path.exists(slideshow_path) or os.path.getsize(slideshow_path) < 1000:
            raise Exception("Slideshow falhou")

        print(f"[MONTAR] Slideshow criado: {os.path.getsize(slideshow_path)} bytes")

        # ── 6. Overlay: título + watermark ───────────────────────────
        if req.overlay_titulo or req.watermark_text:
            overlaid_path = os.path.join(tmpdir, "overlaid.mp4")
            vf_parts = []

            if req.overlay_titulo:
                t = sanitizar_titulo(req.titulo)
                # Título menor e posicionado no centro inferior, visível nos primeiros 5s
                vf_parts.append(
                    f"drawtext=text='{t}'"
                    f":fontsize=36:fontcolor=white"
                    f":x=(w-text_w)/2:y=h*0.82"
                    f":shadowcolor=black@0.9:shadowx=2:shadowy=2"
                    f":box=1:boxcolor=black@0.6:boxborderw=10"
                    f":enable='between(t,0.5,5)'"
                )

            if req.watermark_text:
                wm = req.watermark_text.replace("'", "")
                vf_parts.append(
                    f"drawtext=text='{wm}':fontsize=16:fontcolor=white@0.5"
                    f":x=w-text_w-15:y=h-text_h-15"
                )

            result = subprocess.run([
                "ffmpeg", "-y", "-i", slideshow_path,
                "-vf", ",".join(vf_parts),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                overlaid_path
            ], capture_output=True)

            if result.returncode == 0 and os.path.exists(overlaid_path) and os.path.getsize(overlaid_path) > 1000:
                slideshow_path = overlaid_path

        # ── 7. Merge vídeo + narração com loop ───────────────────────
        video_path = os.path.join(tmpdir, "video_final.mp4")
        dur_slide = get_duracao_ffprobe(slideshow_path)

        print(f"[MONTAR] dur_slide={dur_slide:.1f}s, duracao_total={duracao_total:.1f}s")

        if dur_slide < duracao_total:
            # Loop do slideshow até cobrir toda a narração
            result = subprocess.run([
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", slideshow_path,
                "-i", audio_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-map", "0:v", "-map", "1:a",
                video_path
            ], capture_output=True)
        else:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", slideshow_path, "-i", audio_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-t", str(duracao_total),  # Garante que não corta o áudio
                "-map", "0:v", "-map", "1:a",
                video_path
            ], capture_output=True)

        if result.returncode != 0:
            print(f"[FFMPEG] Erro merge final: {result.stderr.decode()[-500:]}")

        if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
            raise Exception("Vídeo final falhou")

        video_size = os.path.getsize(video_path)
        print(f"[MONTAR] Vídeo final: {video_size} bytes, duração={duracao_total:.1f}s")

        # ── 8. Retornar base64 para o n8n fazer o upload ─────────────
        nome = (
            req.titulo.encode('ascii', 'ignore').decode()
            .replace(' ', '_')[:60] + "_VIDEO.mp4"
        )

        with open(video_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode()

        return {
            "sucesso": True,
            "video_base64": video_b64,
            "titulo": req.titulo,
            "nome_arquivo": nome,
            "duracao_segundos": round(duracao_total),
            "tamanho_bytes": video_size,
            "formato": "mp4"
        }

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
<h1>Privacy Policy</h1><p>This application does not collect or store personal data from users.</p>
</body></html>"""


@app.get("/")
def health():
    return {"status": "ok", "versao": "fase-b-v1"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
