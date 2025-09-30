# main.py
# Application complète sans optical flow, correctifs :
# - Appel de executer_timelapse AVEC arguments nommés (debut=..., fin=...)
# - Exclusivité claire entre timelapse et autres ressources (décochage état UI)
# - Le reste inchangé : cookies persistants (cookies.py), un seul bouton,
#   intervalle optionnel (par défaut toute la vidéo), choix Compressée/HD,
#   extraction MP4/MP3/WAV/Images avec numérotation, zip final, aperçu sans doublon.

import os
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"

import streamlit as st
import subprocess
import re
import glob
import unicodedata
import shutil
import zipfile
from pathlib import Path
import hashlib
import importlib.util
import cv2

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

def _import_timelapse():
    try:
        import timelapse as tl
        return tl
    except Exception:
        spec = importlib.util.spec_from_file_location("timelapse", str(Path("timelapse.py").resolve()))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)  # type: ignore
        return m

tl = _import_timelapse()

def _import_cookies():
    try:
        import cookies as ck
        return ck
    except Exception:
        spec = importlib.util.spec_from_file_location("cookies", str(Path("cookies.py").resolve()))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)  # type: ignore
        return m

ck = _import_cookies()

BASE_DIR = Path("/tmp/appdata")
REPERTOIRE_SORTIE = BASE_DIR / "fichiers"
REPERTOIRE_TEMP = BASE_DIR / "tmp"
REPERTOIRE_SORTIE.mkdir(parents=True, exist_ok=True)
REPERTOIRE_TEMP.mkdir(parents=True, exist_ok=True)

SEUIL_APERCU_OCTETS = 160 * 1024 * 1024
LONGUEUR_TITRE_MAX = 24
LONGUEUR_PREFIX_ID = 8

def vider_cache():
    st.cache_data.clear()

def ffmpeg_disponible() -> bool:
    try:
        _ = tl.chemin_ffmpeg()
        return True
    except Exception:
        return False

def nettoyer_titre(titre: str) -> str:
    if not titre:
        titre = "video"
    titre = titre.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    remplacement = {'«':'','»':'','“':'','”':'','’':'','‘':'','„':'','"':'',"'":'',
                    ':':'-','/':'-','\\':'-','|':'-','?':'','*':'','<':'','>':'','\u00A0':' '}
    for k, v in remplacement.items():
        titre = titre.replace(k, v)
    titre = unicodedata.normalize('NFKD', titre)
    titre = ''.join(c for c in titre if not unicodedata.combining(c))
    titre = re.sub(r'[^\w\s-]', '', titre, flags=re.UNICODE)
    titre = re.sub(r'\s+', '_', titre.strip())
    if not titre:
        titre = "video"
    return titre[:LONGUEUR_TITRE_MAX]

def generer_nom_base(video_id: str, titre: str) -> str:
    vid = (video_id or "vid")[:LONGUEUR_PREFIX_ID]
    tit = nettoyer_titre(titre)
    return f"{vid}_{tit}"

def renommer_sans_collision(src_path: Path, dest_path_base: Path, ext: str = ".mp4") -> Path:
    candidat = Path(f"{dest_path_base}{ext}")
    i = 1
    while candidat.exists():
        candidat = Path(f"{dest_path_base}_{i}{ext}")
        i += 1
    shutil.move(str(src_path), str(candidat))
    return candidat

def taille_fichier(p: Path):
    try:
        return p.stat().st_size
    except Exception:
        return None

def duree_video_seconds(video_path: Path):
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        return int(round(frames / fps)) if fps > 0 else None
    except Exception:
        return None

def zipper_sur_disque(fichiers, chemin_zip: Path) -> Path:
    with zipfile.ZipFile(str(chemin_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in fichiers:
            f = Path(f)
            if f.is_file():
                zf.write(str(f), arcname=f.name)
    return chemin_zip

def lister_sorties(prefix: str):
    patterns = [
        str(REPERTOIRE_SORTIE / f"{prefix}*.mp4"),
        str(REPERTOIRE_SORTIE / f"{prefix}*.mp3"),
        str(REPERTOIRE_SORTIE / f"{prefix}*.wav"),
        str(REPERTOIRE_SORTIE / f"img1_{prefix}" / "i_*.jpg"),
        str(REPERTOIRE_SORTIE / f"img25_{prefix}" / "i_*.jpg"),
        str(REPERTOIRE_SORTIE / f"img1_full_{prefix}" / "i_*.jpg"),
        str(REPERTOIRE_SORTIE / f"img25_full_{prefix}" / "i_*.jpg"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    files = [Path(p) for p in files]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files

def hash_job(source_id: str, fps: int, intervalle):
    import hashlib
    h = hashlib.sha1()
    h.update(source_id.encode("utf-8"))
    h.update(str(fps).encode("utf-8"))
    if intervalle:
        h.update(f"{intervalle[0]}-{intervalle[1]}".encode("utf-8"))
    return h.hexdigest()[:16]

def telecharger_preparer_video(url: str, cookies_path: Path | None, verbose: bool, qualite: str,
                               utiliser_intervalle: bool, debut: int, fin: int):
    st.write("Téléchargement / préparation de la vidéo en cours...")
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    http_headers = {'User-Agent': user_agent, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.5', 'Referer': 'https://www.youtube.com/'}

    base_opts = {
        'paths': {'home': str(REPERTOIRE_SORTIE)},
        'outtmpl': {'default': '%(id)s.%(ext)s'},
        'noplaylist': True,
        'quiet': not verbose,
        'no_warnings': not verbose,
        'merge_output_format': 'mp4',
        'retries': 10,
        'fragment_retries': 10,
        'continuedl': True,
        'concurrent_fragment_downloads': 1,
        'http_headers': http_headers,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'restrictfilenames': True,
        'trim_file_name': 80,
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'mweb', 'web']}},
    }
    if not verbose:
        class _SilentLogger:
            def debug(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): pass
        base_opts['logger'] = _SilentLogger()

    if utiliser_intervalle:
        base_opts['download_sections'] = [{'section': f"*{debut}-{fin}"}]
        base_opts['force_keyframes_at_cuts'] = True

    if cookies_path:
        base_opts['cookiefile'] = str(cookies_path)

    formats_fallbacks = [
        "bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4]/b",
        "bv*+ba/b"
    ]

    derniere_erreur = None
    info = None
    fichier_final = None

    for fmt in formats_fallbacks:
        ydl_opts = base_opts.copy()
        ydl_opts['format'] = fmt
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                _ = ydl.prepare_filename(info)
            candidats = []
            for ext in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                candidats.extend(REPERTOIRE_SORTIE.glob(f"*.{ext}"))
            if not candidats:
                raise DownloadError("Téléchargement terminé mais aucun fichier détecté (download is empty).")
            candidats.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            fichier_final = candidats[0]
            break
        except Exception as e:
            msg = str(e) or repr(e)
            derniere_erreur = e
            if "403" in msg or "Forbidden" in msg:
                if not cookies_path:
                    return None, None, None, "HTTP 403 détecté. La vidéo est restreinte. Fournis un fichier cookies.txt (Firefox : cookies.txt) puis relance."
                return None, None, None, "HTTP 403 persistant malgré cookies. Vérifie que le cookies.txt est valide et récent."
            continue

    if fichier_final is None:
        return None, None, None, (str(derniere_erreur) if derniere_erreur else "Echec inconnu au téléchargement.")

    video_id = (info.get('id') if info else "vid") or "vid"
    titre_brut = (info.get('title') if info else fichier_final.stem) or "video"
    base_court = generer_nom_base(video_id, titre_brut)

    ext_src = fichier_final.suffix
    src_base = REPERTOIRE_SORTIE / f"{base_court}_src"
    chemin_source_propre = renommer_sans_collision(fichier_final, src_base, ext=ext_src)

    cible = REPERTOIRE_SORTIE / f"{base_court}_video.mp4"

    try:
        ffmpeg = tl.chemin_ffmpeg()
    except Exception as e:
        return None, None, None, f"ffmpeg introuvable : {e}"

    def _run_ffmpeg(args):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    if qualite == "Compressée (1280p, CRF 28)":
        try:
            if utiliser_intervalle:
                _run_ffmpeg([ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", str(chemin_source_propre),
                             "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(cible)])
            else:
                _run_ffmpeg([ffmpeg, "-y", "-i", str(chemin_source_propre),
                             "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(cible)])
        except Exception as e:
            return None, None, None, f"Echec de la compression : {e}"
    else:
        try:
            if utiliser_intervalle:
                _run_ffmpeg([ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", str(chemin_source_propre),
                             "-c", "copy", "-movflags", "+faststart", str(cible)])
            else:
                _run_ffmpeg([ffmpeg, "-y", "-i", str(chemin_source_propre), "-c", "copy", "-movflags", "+faststart", str(cible)])
        except Exception:
            try:
                _run_ffmpeg([ffmpeg, "-y", "-i", str(chemin_source_propre),
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(cible)])
            except Exception as e:
                return None, None, None, f"Echec du remux/transcodage : {e}"

    try:
        if chemin_source_propre.exists():
            chemin_source_propre.unlink()
    except Exception:
        pass

    return str(cible), base_court, info, None

def traiter_local(src_local: Path, base_court: str, qualite: str, utiliser_intervalle: bool, debut: int, fin: int) -> str:
    try:
        ffmpeg = tl.chemin_ffmpeg()
    except Exception as e:
        raise RuntimeError(f"ffmpeg introuvable : {e}")

    cible = REPERTOIRE_SORTIE / f"{base_court}_video.mp4"
    def _run_ffmpeg(args):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    if qualite == "Compressée (1280p, CRF 28)":
        args = [ffmpeg, "-y"]
        if utiliser_intervalle:
            args += ["-ss", str(debut), "-to", str(fin)]
        args += ["-i", str(src_local), "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                 "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(cible)]
        _run_ffmpeg(args)
    else:
        try:
            args = [ffmpeg, "-y"]
            if utiliser_intervalle:
                args += ["-ss", str(debut), "-to", str(fin)]
            args += ["-i", str(src_local), "-c", "copy", "-movflags", "+faststart", str(cible)]
            _run_ffmpeg(args)
        except Exception:
            args = [ffmpeg, "-y"]
            if utiliser_intervalle:
                args += ["-ss", str(debut), "-to", str(fin)]
            args += ["-i", str(src_local), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(cible)]
            _run_ffmpeg(args)
    return str(cible)

def extraire_ressources(video_path: str, debut: int, fin: int, base_court: str, options: dict, utiliser_intervalle: bool):
    try:
        ffmpeg = tl.chemin_ffmpeg()
    except Exception as e:
        return f"ffmpeg introuvable : {e}"

    def _run_ffmpeg(args):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def cmd_segment(sortie: Path):
        if utiliser_intervalle:
            return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(sortie)]
        else:
            return [ffmpeg, "-y", "-i", video_path,
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(sortie)]

    def cmd_audio(sortie: Path, codec_args):
        if utiliser_intervalle:
            return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path] + codec_args + ["-movflags", "+faststart", str(sortie)]
        else:
            return [ffmpeg, "-y", "-i", video_path] + codec_args + ["-movflags", "+faststart", str(sortie)]

    def cmd_images(output_pattern: str, fps: int):
        vf = f"fps={fps},scale=1920:1080"
        if utiliser_intervalle:
            return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]
        else:
            return [ffmpeg, "-y", "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]

    if options.get("mp4"):
        nom = f"{base_court}_seg.mp4" if utiliser_intervalle else f"{base_court}_full.mp4"
        _run_ffmpeg(cmd_segment(REPERTOIRE_SORTIE / nom))

    if options.get("mp3"):
        nom = f"{base_court}_seg.mp3" if utiliser_intervalle else f"{base_court}_full.mp3"
        _run_ffmpeg(cmd_audio(REPERTOIRE_SORTIE / nom, ["-vn", "-acodec", "libmp3lame", "-q:a", "5"]))

    if options.get("wav"):
        nom = f"{base_court}_seg.wav" if utiliser_intervalle else f"{base_court}_full.wav"
        _run_ffmpeg(cmd_audio(REPERTOIRE_SORTIE / nom, ["-vn", "-acodec", "adpcm_ima_wav"]))

    if options.get("img1") or options.get("img25"):
        for fps in [1, 25]:
            if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                dossier = f"img{fps}_{base_court}" if utiliser_intervalle else f"img{fps}_full_{base_court}"
                rep = REPERTOIRE_SORTIE / dossier
                rep.mkdir(parents=True, exist_ok=True)
                tmp_pattern = str(rep / "tmp_%06d.jpg")
                _run_ffmpeg(cmd_images(tmp_pattern, fps))
                images_gen = sorted(rep.glob("tmp_*.jpg"))
                start_offset = debut if utiliser_intervalle else 0
                for i, src in enumerate(images_gen):
                    t = start_offset + (i / float(fps))
                    sec = int(t)
                    if fps == 1:
                        nom_cible = f"i_{sec}s_1fps.jpg"
                    else:
                        f_in_s = int(round((t - sec) * fps))
                        if f_in_s >= fps:
                            f_in_s = fps - 1
                        nom_cible = f"i_{sec}s_{fps}fps_{f_in_s:02d}.jpg"
                    dst = rep / nom_cible
                    j = 1
                    base_dst = dst.with_suffix("")
                    ext = dst.suffix
                    while dst.exists():
                        dst = Path(f"{base_dst}_{j}{ext}")
                        j += 1
                    os.replace(str(src), str(dst))

    return None

# ---------------- Interface utilisateur ----------------

st.title("Extraction multimédia (vidéo, audio, images)")
st.markdown("**[www.codeandcortex.fr](http://www.codeandcortex.fr)**")

vider_cache()

st.markdown(
    "Par défaut, l’extraction porte sur **toute la vidéo**. Vous pouvez activer un intervalle personnalisé si besoin. "
    "Si la vidéo est restreinte (403), exportez vos cookies avec l’extension Firefox : "
    "[cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)."
)

with st.expander("Diagnostic système"):
    try:
        chemin = tl.chemin_ffmpeg()
        ver = subprocess.run([chemin, "-version"], capture_output=True, text=True, check=False)
        st.write(f"ffmpeg : {chemin}")
        if ver.stdout:
            st.code(ver.stdout.splitlines()[0])
    except Exception as e:
        st.write(f"ffmpeg : introuvable ({e})")
    # état cookies
    try:
        import cookies as _ckdiag
        from pathlib import Path as _Path
        st.write(_ckdiag.info_cookies(_Path("/tmp/appdata/fichiers")))
    except Exception:
        pass

# Etats
st.session_state.setdefault("debut_secs", 0)
st.session_state.setdefault("fin_secs", 10)
st.session_state.setdefault("video_base", None)
st.session_state.setdefault("base_court", None)
st.session_state.setdefault("apercu_local_bytes", None)
st.session_state.setdefault("upload_signature", None)
st.session_state.setdefault("local_temp_path", None)
st.session_state.setdefault("local_name_base", None)

# Source
url = st.text_input("URL YouTube")
cookies_path_eff = ck.afficher_section_cookies(REPERTOIRE_SORTIE)
fichier_local = st.file_uploader("Ou importer un fichier vidéo (.mp4)", type=["mp4"])

# Options
mode_verbose = st.checkbox("Mode diagnostic yt-dl", value=False)
qualite = st.radio("Qualité de la vidéo de base", ["Compressée (1280p, CRF 28)", "HD (max qualité dispo)"], index=0)

st.subheader("Ressources à produire")
st.markdown("<style>div[data-testid='stHorizontalBlock'] label { white-space: nowrap; }</style>", unsafe_allow_html=True)
c1, c2, c3, c4, c5, c6 = st.columns([1,1,1,1,1,1])
with c1: opt_mp4 = st.checkbox("MP4", key="opt_mp4")
with c2: opt_mp3 = st.checkbox("MP3", key="opt_mp3")
with c3: opt_wav = st.checkbox("WAV", key="opt_wav")
with c4: opt_img1 = st.checkbox("Img 1 FPS", key="opt_img1")
with c5: opt_img25 = st.checkbox("Img 25 FPS", key="opt_img25")
with c6: opt_timelapse = st.checkbox("Timelapse", key="opt_timelapse")

# Exclusivité claire : si timelapse est coché, on nettoie l’état des autres cases
if opt_timelapse:
    for k in ("opt_mp4", "opt_mp3", "opt_wav", "opt_img1", "opt_img25"):
        if st.session_state.get(k):
            st.session_state[k] = False
    st.info("Mode timelapse activé : l’application n’affichera aucune vidéo. Elle générera uniquement le fichier timelapse à télécharger.")
    fps_timelapse = st.selectbox("FPS timelapse", [4, 6, 8, 10, 12, 14, 16], index=2, key="fps_timelapse")
else:
    fps_timelapse = 12

st.subheader("Étendue")
etendue = st.radio("Choisir l’étendue", ["Toute la vidéo", "Intervalle personnalisé"], index=0)
if etendue == "Intervalle personnalisé":
    st.info(f"Intervalle personnalisé activé : de {st.session_state['debut_secs']}s à {st.session_state['fin_secs']}s. Le téléchargement traitera uniquement cet intervalle.")
    cc1, cc2 = st.columns(2)
    st.session_state["debut_secs"] = cc1.number_input("Début (s)", min_value=0, value=st.session_state["debut_secs"])
    st.session_state["fin_secs"] = cc2.number_input("Fin (s)", min_value=1, value=st.session_state["fin_secs"])
    utiliser_intervalle = True
    if st.session_state["fin_secs"] <= st.session_state["debut_secs"]:
        st.warning("La fin doit être strictement supérieure au début.")
else:
    utiliser_intervalle = False

# Aperçu vidéo uniquement si timelapse n’est pas sélectionné
afficher_apercu = st.checkbox("Afficher l’aperçu vidéo", value=True, disabled=opt_timelapse)
if afficher_apercu and not opt_timelapse:
    if st.session_state.get('video_base') and Path(st.session_state['video_base']).exists():
        size = taille_fichier(Path(st.session_state['video_base'])) or 0
        if size <= SEUIL_APERCU_OCTETS:
            with open(st.session_state['video_base'], "rb") as f:
                st.video(f.read(), format="video/mp4")
    elif fichier_local is not None:
        signature = f"{fichier_local.name}-{fichier_local.size}"
        if signature != st.session_state['upload_signature']:
            tmp = REPERTOIRE_TEMP / f"local_upload_{signature}.mp4"
            with open(tmp, "wb") as g:
                g.write(fichier_local.read())
            st.session_state['upload_signature'] = signature
            st.session_state['apercu_local_bytes'] = None
            try:
                data = tmp.read_bytes()
                st.session_state['apercu_local_bytes'] = data if len(data) <= SEUIL_APERCU_OCTETS else b""
            except Exception:
                st.session_state['apercu_local_bytes'] = b""
            st.session_state['local_temp_path'] = str(tmp)
            st.session_state['local_name_base'] = generer_nom_base("local", Path(fichier_local.name).stem)
        if st.session_state.get('apercu_local_bytes'):
            st.video(st.session_state['apercu_local_bytes'], format="video/mp4")
        else:
            st.info("Fichier local volumineux : aperçu désactivé (lance le traitement).")
    elif url:
        st.info("Aperçu indisponible pour une URL tant que le traitement n’a pas été lancé.")

# Bouton unique
if st.button("Lancer le traitement"):
    with st.spinner("Traitement en cours..."):
        if not ffmpeg_disponible():
            st.error("ffmpeg introuvable et fallback impossible (réseau bloqué ?). Ajoute 'imageio-ffmpeg' dans requirements.txt ou autorise le réseau.")
        else:
            # Préparation de la vidéo de base
            if url:
                video_base, base_court, info, err = telecharger_preparer_video(
                    url, cookies_path_eff, mode_verbose, qualite, utiliser_intervalle,
                    st.session_state["debut_secs"], st.session_state["fin_secs"]
                )
                if err:
                    st.error(f"Erreur : {err}")
                    video_base = None
                else:
                    st.session_state['video_base'] = video_base
                    st.session_state['base_court'] = base_court
                    st.success(f"Vidéo prête : {Path(video_base).name}")
            elif st.session_state.get('local_temp_path'):
                base_court = st.session_state.get('local_name_base') or generer_nom_base("local", "video")
                try:
                    cible = traiter_local(Path(st.session_state['local_temp_path']), base_court, qualite, utiliser_intervalle,
                                          st.session_state["debut_secs"], st.session_state["fin_secs"])
                    st.session_state['video_base'] = cible
                    st.session_state['base_court'] = base_court
                    st.success(f"Vidéo prête : {Path(cible).name}")
                except Exception as e:
                    st.error(f"Echec du traitement local : {e}")
            else:
                st.warning("Veuillez fournir une URL YouTube ou un fichier local.")

            # Production
            if st.session_state.get('video_base') and Path(st.session_state['video_base']).exists():
                base_court = st.session_state['base_court']
                video_path = st.session_state['video_base']

                if st.session_state.get("opt_timelapse", False):
                    try:
                        intervalle = (st.session_state["debut_secs"], st.session_state["fin_secs"]) if utiliser_intervalle else None
                        job_id = hash_job(f"file:{video_path}", st.session_state.get("fps_timelapse", 12), intervalle)
                        out_path, nb_images = tl.executer_timelapse(
                            video_path, job_id, base_court, st.session_state.get("fps_timelapse", 12),
                            debut=st.session_state["debut_secs"] if utiliser_intervalle else None,
                            fin=st.session_state["fin_secs"] if utiliser_intervalle else None
                        )
                        st.success(f"Timelapse généré ({nb_images} images).")
                        with open(out_path, "rb") as fh:
                            st.download_button("Télécharger le timelapse (.mp4)", data=fh, file_name=Path(out_path).name, mime="video/mp4")
                    except Exception as e:
                        st.error(f"Echec du timelapse : {e}")
                else:
                    if utiliser_intervalle:
                        debut_eff = st.session_state["debut_secs"]
                        fin_eff = st.session_state["fin_secs"]
                    else:
                        duree = duree_video_seconds(Path(video_path)) or 0
                        debut_eff, fin_eff = 0, duree

                    options = {
                        "mp4": st.session_state.get("opt_mp4", False),
                        "mp3": st.session_state.get("opt_mp3", False),
                        "wav": st.session_state.get("opt_wav", False),
                        "img1": st.session_state.get("opt_img1", False),
                        "img25": st.session_state.get("opt_img25", False)
                    }
                    if any(options.values()):
                        err2 = extraire_ressources(video_path, debut_eff, fin_eff, base_court, options, utiliser_intervalle)
                        if err2:
                            st.error(f"Erreur pendant l'extraction : {err2}")
                        else:
                            st.success("Ressources générées.")

                    fichiers = lister_sorties(base_court)
                    if Path(video_path) not in fichiers:
                        fichiers.append(Path(video_path))
                    zip_path = REPERTOIRE_SORTIE / f"resultats_{base_court}.zip"
                    zipper_sur_disque(fichiers, zip_path)
                    with open(zip_path, "rb") as fh:
                        st.download_button("Télécharger les résultats (.zip)", data=fh, file_name=zip_path.name, mime="application/zip")
