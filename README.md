# README

Extraction d'un vidéo Youtube ou depuis un fichier mp4 local : wwww.codeandcortex.fr


## Prérequis système

* **ffmpeg** installé sur votre Mac (nécessaire pour la compression vidéo via `subprocess`).

## Installation des dépendances Python

Dans votre terminal de votre editeur de code, exécutez :

```bash
pip install streamlit yt-dlp
```

> **Remarque** : les modules `os`, `subprocess`, `re` et `glob` font partie de la bibliothèque standard Python.


## Structure du script

1. **Compression vidéo** :

   * Appel à `ffmpeg` via `subprocess` pour :

     * redimensionner la vidéo à 1280 px de largeur
     * encoder la vidéo avec CRF=28
     * encoder l’audio en AAC à 96 kbps
   * Résultat stocké dans `ressources_globale`
     
2. **Extraction par intervalle** :

   * L’utilisateur définit un intervalle (*start*, *end*)
   * Extraction de ressources (mp4, audio mp3 et wav, images 1fps ou 25fps)
   * Stockage dans `ressources_intervalle`

## Fonctionnalités principales

* **Téléchargement YouTube** : téléchargement d’une vidéo via `yt_dlp.YoutubeDL`, avec prise en charge facultative d’un fichier de cookies.
* **Import local** : chargement d’un fichier mp4 depuis l’ordinateur.
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

---
