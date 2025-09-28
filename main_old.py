# pip install streamlit yt_dlp

# ---------------- Imports ----------------
import streamlit as st
import os
import subprocess
import re
import glob
from yt_dlp import YoutubeDL

# ---------------- Fonctions ----------------

# Nettoyage du cache Streamlit
def vider_cache():
    st.cache_data.clear()

# Nettoyage du titre de la vidéo pour en faire un nom de fichier
def nettoyer_titre(titre):
    titre_nettoye = re.sub(r'[^\w\s-]', '', titre).strip().replace(' ', '_')
    return titre_nettoye[:50]

# Téléchargement et compression automatique de la vidéo
def telecharger_video(url, repertoire, cookies_path=None):
    st.write("Téléchargement de la vidéo compressée en cours...")

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    ydl_opts = {
        'outtmpl': os.path.join(repertoire, '%(title)s.%(ext)s'),
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'user_agent': user_agent,
    }

    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title_brut = info.get('title', 'video')
            video_title = nettoyer_titre(video_title_brut)

        video_candidates = []
        for ext in ['mp4', 'mkv', 'webm']:
            video_candidates += glob.glob(os.path.join(repertoire, f"*.{ext}"))

        if not video_candidates:
            return None, None, f"Aucun fichier vidéo trouvé dans {repertoire}"

        video_candidates.sort(key=os.path.getmtime, reverse=True)
        original_path = video_candidates[0]
        video_title = os.path.splitext(os.path.basename(original_path))[0]

        compressed_path = os.path.join(repertoire, f"{video_title}_compressed.mp4")

        # Compression vidéo globale
        subprocess.run([
            "ffmpeg", "-y", "-i", original_path,
            "-vf", "scale=1280:-2",             # ↓ réduction de résolution (largeur max 1280)
            "-c:v", "libx264",                  # codec vidéo
            "-preset", "slow",                  # preset lent = compression plus efficace
            "-crf", "28",                       # CRF 28 = qualité moyenne, poids réduit (18 = top, 30 = très compressé)
            "-c:a", "aac", "-b:a", "96k",       # audio compressé AAC 96 kbps
            compressed_path
        ], check=True)

        return compressed_path, video_title, None

    except Exception as e:
        return None, None, str(e)

# Extraction des ressources à partir de la vidéo compressée
def extraire_ressources(video_path, repertoire, debut, fin, video_title, options):
    try:
        if options.get("mp4"):
            extrait_video_path = os.path.join(repertoire, f"{video_title}_extrait.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                extrait_video_path
            ], check=True)

        if options.get("mp3"):
            extrait_mp3_path = os.path.join(repertoire, f"{video_title}_extrait.mp3")
            subprocess.run(["ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                            "-vn", "-acodec", "libmp3lame", "-q:a", "5", extrait_mp3_path], check=True)

        if options.get("wav"):
            extrait_wav_path = os.path.join(repertoire, f"{video_title}_extrait.wav")
            subprocess.run(["ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                            "-vn", "-acodec", "adpcm_ima_wav", extrait_wav_path], check=True)

        if options.get("img1") or options.get("img25"):
            for fps in [1, 25]:
                if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                    images_repertoire = os.path.join(repertoire, f"images_{fps}fps_{video_title}")
                    os.makedirs(images_repertoire, exist_ok=True)
                    output_pattern = os.path.join(images_repertoire, "image_%04d.jpg")
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                        "-vf", f"fps={fps},scale=1920:1080", "-q:v", "1", output_pattern
                    ], check=True)

        return None

    except Exception as e:
        return str(e)

# ---------------- Interface utilisateur ----------------

st.title("Extraction multimédia compressée (mp4 - mp3 - wav - images)")
st.markdown("**[www.codeandcortex.fr](http://www.codeandcortex.fr)**")

vider_cache()

st.markdown("""
➡ Entrez une URL YouTube **ou** importez un fichier mp4.  
➡ La vidéo est compressée (1280px, CRF 28, AAC 96kbps) et enregistrée dans **ressources_globale**.  
➡ Vous pouvez ensuite définir un intervalle d'extraction et choisir les ressources à extraire dans **ressources_intervalle**.
""")

url = st.text_input("Entrez l'URL de la vidéo YouTube :")
fichier_local = st.file_uploader("Ou importez un fichier vidéo (.mp4)", type=["mp4"])
cookies_file = st.file_uploader("Uploader votre fichier cookies.txt (optionnel)", type=["txt"])

repertoire_globale = os.path.abspath("ressources_globale")
repertoire_intervalle = os.path.abspath("ressources_intervalle")
os.makedirs(repertoire_globale, exist_ok=True)
os.makedirs(repertoire_intervalle, exist_ok=True)

if st.button("Lancer le téléchargement"):
    if url:
        cookies_path = None
        if cookies_file:
            cookies_path = os.path.join(repertoire_globale, "cookies.txt")
            with open(cookies_path, "wb") as f:
                f.write(cookies_file.read())

        video_path, video_title, erreur = telecharger_video(url, repertoire_globale, cookies_path)

        if erreur:
            st.error(f"Erreur : {erreur}")
        else:
            st.session_state['video_path'] = video_path
            st.session_state['video_title'] = video_title
            st.success("Vidéo compressée téléchargée avec succès dans ressources_globale.")

    elif fichier_local:
        video_title = os.path.splitext(fichier_local.name)[0]
        original_path = os.path.join(repertoire_globale, fichier_local.name)
        compressed_path = os.path.join(repertoire_globale, f"{video_title}_compressed.mp4")
        with open(original_path, "wb") as f:
            f.write(fichier_local.read())

        subprocess.run(["ffmpeg", "-y", "-i", original_path,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", compressed_path])
        st.session_state['video_path'] = compressed_path
        st.session_state['video_title'] = video_title
        st.success("Vidéo locale compressée avec succès dans ressources_globale.")
    else:
        st.warning("Veuillez fournir une URL YouTube ou un fichier local.")

# Extraction si vidéo présente
if 'video_path' in st.session_state and os.path.exists(st.session_state['video_path']):
    st.markdown("---")
    st.video(st.session_state['video_path'])
    st.subheader("Paramètres d'extraction (ressources_intervalle)")

    col1, col2 = st.columns(2)
    debut = col1.number_input("Début (en secondes)", min_value=0, value=0)
    fin = col2.number_input("Fin (en secondes)", min_value=1, value=10)

    st.markdown("**Choisissez les ressources à extraire :**")
    opt_mp4 = st.checkbox("Vidéo MP4")
    opt_mp3 = st.checkbox("Audio MP3")
    opt_wav = st.checkbox("Audio WAV")
    opt_img1 = st.checkbox("Images 1 FPS")
    opt_img25 = st.checkbox("Images 25 FPS")

    if st.button("Extraire les ressources"):
        options = {
            "mp4": opt_mp4,
            "mp3": opt_mp3,
            "wav": opt_wav,
            "img1": opt_img1,
            "img25": opt_img25
        }
        erreur = extraire_ressources(
            st.session_state['video_path'],
            repertoire_intervalle,
            debut,
            fin,
            st.session_state['video_title'],
            options
        )
        if erreur:
            st.error(f"Erreur pendant l'extraction : {erreur}")
        else:
            st.success("Ressources extraites avec succès dans le répertoire ressources_intervalle.")

