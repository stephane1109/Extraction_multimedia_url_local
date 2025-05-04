# README

## Préambule

Ce README décrit les dépendances et les fonctionnalités du script Streamlit destiné à importer et traiter des vidéos YouTube ou locales, en reprenant précisément les bibliothèques utilisées dans votre code.

## Prérequis système

* **Python** 3.8 ou supérieur
* **ffmpeg** installé et disponible dans votre PATH système (nécessaire pour la compression vidéo via `subprocess`).

## Installation des dépendances Python

Dans un terminal, exécutez :

```bash
pip install streamlit yt-dlp
```

> **Remarque** : les modules `os`, `subprocess`, `re` et `glob` font partie de la bibliothèque standard Python.

## Imports dans le script

```python
# ---------------- Imports ----------------
import streamlit as st
import os
import subprocess
import re
import glob
from yt_dlp import YoutubeDL
```

## Structure du script

1. **Entrée vidéo** :

   * `st.text_input` pour l’URL YouTube
   * `st.file_uploader` pour un fichier local MP4
   * `st.file_uploader` pour un fichier `cookies.txt` (optionnel)
2. **Compression vidéo** :

   * Appel à `ffmpeg` via `subprocess` pour :

     * redimensionner la vidéo à 1280 px de largeur
     * encoder la vidéo avec CRF=28
     * encoder l’audio en AAC à 96 kbps
   * Résultat stocké dans `ressources_globale`
3. **Extraction par intervalle** :

   * L’utilisateur définit un intervalle (*start*, *end*)
   * Extraction de ressources (audio, images, sous-titres…) via des expressions régulières et `glob`
   * Stockage dans `ressources_intervalle`

### Variables clés

```python
url = st.text_input("Entrez l'URL de la vidéo YouTube :")
fichier_local = st.file_uploader("Ou importez un fichier vidéo (.mp4)", type=["mp4"])
cookies_file = st.file_uploader("Uploader votre fichier cookies.txt (optionnel)", type=["txt"])

repertoire_globale = os.path.abspath("ressources_globale")
repertoire_intervalle = os.path.abspath("ressources_intervalle")
```

## Fonctionnalités principales

* **Téléchargement YouTube** : téléchargement d’une vidéo via `yt_dlp.YoutubeDL`, avec prise en charge facultative d’un fichier de cookies pour les contenus privés.
* **Import local** : chargement d’un fichier MP4 depuis l’ordinateur.
* **Compression vidéo** : exécution d’une commande `ffmpeg` adaptée aux paramètres suivants :

  * largeur maximale : **1280 px**
  * qualité CRF : **28**
  * audio AAC : **96 kbps**
* **Extraction d’intervalle** : sélection d’un segment temporel et extraction de ressources spécifiques, stockées dans `ressources_intervalle`.

## Lancement de l’application

Dans votre terminal, lancez :

```bash
streamlit run nom_du_script.py
```

L’interface Streamlit s’ouvrira automatiquement dans votre navigateur.

---

> Pour toute question ou suggestion, ouvrez une issue ou contactez l’
