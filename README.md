# Helius Quota Burner – Railway Deployment

**⚠️ EXTREM WICHTIGE WARNUNG**

Dieses Tool ist dafür gemacht, Helius API Keys **extrem schnell** zu verbrennen (hohe RPS mit teuren DAS- und Program-Account-Calls).

- Nur für **eigene Keys** oder Keys, für die du **explizite Erlaubnis** hast.
- Das Verbrennen fremder / geleakter Keys kann gegen die AGB von Helius verstoßen und rechtliche Konsequenzen haben.
- Der Ersteller dieses Tools übernimmt **keine Verantwortung**.

---

## Schnell-Deployment auf Railway

### 1. Vorbereitung
- Erstelle ein neues Git-Repository (oder nutze ein bestehendes)
- Kopiere den gesamten Ordner `helius-burner-railway/` hinein (oder pushe diesen Ordner als Root)

### 2. Auf Railway deployen

1. Gehe zu [railway.app](https://railway.app) und logge dich ein.
2. **New Project** → **Deploy from GitHub**.
3. Wähle dein Repo aus.
4. Railway erkennt automatisch das `Dockerfile` (oder du kannst auf Nixpacks umstellen).

### 3. Wichtige Environment Variables setzen

Im Railway Dashboard → dein Service → **Variables**:

| Variable       | Wert-Beispiel                          | Beschreibung                          |
|----------------|----------------------------------------|---------------------------------------|
| `HELIUS_KEYS`  | `key1,key2,key3`                       | **Pflicht!** Komma-getrennte Helius Keys |
| `CONCURRENCY`  | `200`                                  | Anzahl paralleler Worker (je höher = mehr Verbrauch) |
| `MODE`         | `mixed` oder `expensive` oder `das-heavy` | `expensive` und `mixed` sind am aggressivsten |
| `DURATION`     | `0`                                    | `0` = unendlich laufen (empfohlen für Railway) |

**Empfohlene aggressive Einstellung:**
- `CONCURRENCY=250`
- `MODE=expensive`
- `DURATION=0`

### 4. Deploy starten
- Nach dem Setzen der Variablen → **Deploy** klicken.
- Im **Logs** Tab siehst du die RPS-Ausgabe alle 5 Sekunden.

Railway startet das als `worker` Prozess (siehe Procfile). Es läuft persistent, bis du den Service stoppst oder die Keys leer sind / rate-limited werden.

---

## Lokales Testen

```bash
cd helius-burner-railway
pip install -r requirements.txt

# Mit Env Vars (wie auf Railway)
HELIUS_KEYS=dein_key CONCURRENCY=50 MODE=mixed DURATION=60 python helius_quota_stress.py
```

---

## Tipps für maximalen Verbrauch

- Höhere `CONCURRENCY` (300–500+ je nach Railway Plan)
- `MODE=expensive` → viele `getProgramAccounts` ohne Filter (sehr teuer)
- Mehrere Railway Services mit unterschiedlichen Keys parallel laufen lassen
- `KNOWN_MINTS` / `KNOWN_PROGRAMS` im Script erweitern mit mehr echten Adressen

**Achtung:** Railway selbst hat Limits (CPU, Memory, Egress). Zu hohe Concurrency kann dazu führen, dass der Container throttled oder abstürzt.

---

## Struktur

```
helius-burner-railway/
├── helius_quota_stress.py   # Der eigentliche Burner (env-var ready)
├── requirements.txt
├── Dockerfile
├── Procfile
├── railway.toml
└── README.md
```

Viel Erfolg (und sei vorsichtig mit fremden Keys).
