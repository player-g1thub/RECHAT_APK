
# Realtime Chat (Kivy)

This is a Kivy port of your realtime chat (originally PyQt5).  
It can run on desktop and be packaged to Android APK using **Buildozer** and GitHub Actions.

## Run locally (desktop)
```bash
pip install kivy pillow
python main.py
```

## Host & Connect
- Press **Save ID/Name**.
- On one device, press **Host** (starts server on port 6000 and auto-connects).
- On other devices, press **Connect**, fill host IP and port (6000 by default).
- Select a contact from roster (tap list) to DM, or leave target empty to broadcast.

## Build APK via GitHub Actions
1. Put these files at repo root: `main.py`, `buildozer.spec`, `.github/workflows/android.yml`.
2. Commit and push to `main` (or trigger **Run workflow** manually).
3. After workflow runs, download APK from **Artifacts** named `rechat-apk` (file under `bin/`).

Uses [ArtemSBulgakov/buildozer-action@v1](https://github.com/ArtemSBulgakov/buildozer-action).
