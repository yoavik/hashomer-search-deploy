# HaShomer Semantic Search — Deployment Log

**תאריך:** 9 באפריל 2026
**מבוסס על:** [AmirSchwarz/hashomer_search_deployment](https://github.com/AmirSchwarz/hashomer_search_deployment) (private)
**Fork ציבורי:** [yoavik/hashomer-search-deploy](https://github.com/yoavik/hashomer-search-deploy)
**EC2:** ip-172-31-66-99 (אותו שרת production — ראה הערה בהמשך)

---

## סקירת הארכיטקטורה

### 7-Signal Hybrid Search
חיפוש סמנטי שמשלב 7 אותות באמצעות Reciprocal Rank Fusion (RRF):
1. Sentence semantic (e5-large, 1024d)
2. Segment semantic
3. Sentence keyword FTS
4. Segment keyword FTS
5. Phrase match
6. Trigram fuzzy
7. Topic expansion

**Reranking:** HeCross cross-encoder על top-20 תוצאות.

### רכיבים
- **Supabase PostgreSQL** — pgvector עם halfvec(1024), סכמה מלאה כבר קיימת (~3,155 media records, ~24K segments)
- **EC2 Docker services** — 4 containers: caddy, embed, search, index
- **RunPod Serverless** — diarization (pyannote 3.1) + audio segmentation
- **Pipeline cron** — סורק S3 לתמלולים חדשים → diarize → index

### היררכיית נתונים
Sentence → Segment (קיבוץ לפי cosine similarity > 0.55) → Topic

---

## שלב 1: הכנת EC2 (Docker Services)

### 1.1 התקנת Docker Compose

**בעיה:** docker compose לא נמצא — ה-EC2 הריץ Docker מ-Ubuntu repos (docker.io), לא מ-Docker official.

**פתרון:**
```bash
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install docker-compose-plugin
```

**הערה:** במהלך apt-get update צצו דיאלוגים של kernel update ו-service restart — פשוט לאשר ולהמשיך.

### 1.2 Port 80 תפוס על ידי nginx

**בעיה:** Caddy container לא הצליח לעשות bind ל-port 80 כי nginx כבר רץ.

**פתרון:**
```bash
sudo systemctl stop nginx
sudo systemctl disable nginx
```

### 1.3 Caddy HTTPS Redirect (308)

**בעיה:** ה-Caddyfile המקורי היה מוגדר לדומיין עם auto-HTTPS. curl מקומי קיבל redirect 308.

**פתרון:** שכתוב Caddyfile ל-dev (:80 במקום דומיין):
```
:80 {
    handle /api/search* { reverse_proxy search:8000 }
    handle /api/embed/* { uri strip_prefix /api/embed; reverse_proxy embed:8000 }
    handle /api/index/* { uri strip_prefix /api/index; reverse_proxy index:8000 }
    handle /api/health { reverse_proxy search:8000 }
    handle { root * /srv; file_server; try_files {path} /index.html }
}
```

**מיקום הקובץ על EC2:** ~/app/services/Caddyfile

### 1.4 Timeout בבקשה ראשונה

**בעיה:** חיפוש ראשון נתקע — embed service טוען מודלים (e5-large + HeCross) ב-cold start.

**פתרון:** להמתין. אחרי ה-warmup הראשון הכל עובד עם timeout של 120s.

### 1.5 Supabase — anon key במקום service_role

**בעיה:** הודבק anon key במקום service_role key.

**איך לזהות:** פענוח JWT — שדה role בתוך הטוקן:
- "role": "anon" → anon key (לא מתאים)
- "role": "service_role" → service_role key (מה שצריך)

**פתרון:** Supabase Dashboard → Settings → API → service_role (מוסתר כברירת מחדל, צריך ללחוץ Reveal).

---

## שלב 2: הרמת Services

### 2.1 מבנה הקבצים על EC2
```
~/app/services/
├── Caddyfile
├── docker-compose.yml
├── .env
├── embed/ (Dockerfile, app.py, embeddings.py, requirements.txt)
├── search/ (Dockerfile, app.py, requirements.txt)
├── index/ (Dockerfile, app.py, indexer.py, requirements.txt)
└── shared/ (__init__.py, config.py, s3.py, supabase_client.py, text_processing.py)
```

### 2.2 Environment Variables (.env)
```
SUPABASE_URL=<supabase project url>
SUPABASE_KEY=<service_role JWT>
AWS_KEY_ID=<aws access key>
AWS_SECRET=<aws secret key>
S3_BUCKET=yoav-radio-recordings
S3_REGION=us-east-1
OPENAI_API_KEY=<openai key>
RUNPOD_API_KEY=<runpod api key - must be All permissions>
RUNPOD_DIARIZE_ENDPOINT=<diarize endpoint id>
RUNPOD_AUDIO_SEG_ENDPOINT=<audio seg endpoint id>
```

### 2.3 הפעלה
```bash
cd ~/app/services
docker compose up -d --build
docker compose logs -f
```

### 2.4 אימות
```bash
curl -s http://localhost/api/health | python3 -m json.tool
curl -s -X POST http://localhost/api/search -H "Content-Type: application/json" \
  -d '{"query": "חטופים", "mode": "hybrid", "limit": 3}' | python3 -m json.tool
```

**סטטוס:** עובד — כל 4 containers רצים, חיפוש מחזיר תוצאות מ-~24K segments.

---

## שלב 3: RunPod Endpoints

### 3.1 הכנה — Fork ציבורי

**בעיה:** הרפו של אמיר private, ו-RunPod לא יכול לגשת אליו. Fork של private repo גם הוא private ואי אפשר להפוך אותו ל-public.

**פתרון:** יצירת repo חדש (לא fork) ב-GitHub — yoavik/hashomer-search-deploy (public).

### 3.2 תיקון Dockerfile

**בעיה:** runpod/src/Dockerfile.runpod הפנה ל-diarize/requirements.txt ו-diarize/*.py שלא קיימים.

**פתרון:** תוקן ל:
```dockerfile
FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt runpod
COPY shared ./shared
COPY *.py .
CMD ["python", "handler.py"]
```

### 3.3 Endpoint 1 — Diarization

| הגדרה | ערך |
|--------|------|
| Repo | yoavik/hashomer-search-deploy |
| Branch | main |
| Dockerfile path | runpod/src/Dockerfile.runpod |
| Build context | runpod/src |
| Endpoint name | hashomer-diarize-dev |
| GPU | 32GB PRO (High Supply) מומלץ |
| Min/Max Workers | 0 / 1 |
| Execution Timeout | 600s |

**Environment Variables:** AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET=yoav-radio-recordings, HF_TOKEN

**חשוב — אישור גישה ל-Hugging Face Models (חד פעמי):**
1. https://huggingface.co/pyannote/speaker-diarization-3.1
2. https://huggingface.co/pyannote/segmentation-3.0

### 3.4 Endpoint 2 — Audio Segmentation

| הגדרה | ערך |
|--------|------|
| Repo | yoavik/hashomer-search-deploy |
| Branch | main |
| Dockerfile path | audio-processing/src/Dockerfile.runpod |
| Build context | audio-processing/src |
| Endpoint name | hashomer-audio-seg-dev |
| GPU | 16GB+ |
| Min/Max Workers | 0 / 1 |
| Execution Timeout | 300s |

**Environment Variables:** AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET=yoav-radio-recordings

### 3.5 שגיאות RunPod ופתרונות

**403 Forbidden:** RunPod API key עם הרשאת Read בלבד → שינוי ל-All permissions.

**Worker Throttled:** GPU 16GB עם Low Supply → שינוי ל-32GB PRO (High Supply).

**GatedRepoError (pyannote/segmentation-3.0):** Worker נכשל עם 403 Cannot access gated repo → יש לאשר גישה גם למודל segmentation-3.0 ב-Hugging Face.

---

## שלב 4: Pipeline

### 4.1 מבנה הקבצים על EC2
```
~/app/pipeline/
├── .env
└── trigger_pipeline.py
```

### 4.2 Pipeline .env
```
AWS_KEY_ID=<aws key>
AWS_SECRET=<aws secret>
S3_BUCKET=yoav-radio-recordings
S3_REGION=us-east-1
RUNPOD_API_KEY=<runpod api key>
RUNPOD_DIARIZE_ENDPOINT=<endpoint id>
INDEX_SERVICE_URL=http://localhost/api/index
```

### 4.3 שימוש
```bash
cd ~/app/pipeline
python3 trigger_pipeline.py --dry-run --station kan-bet --hours 24  # dry-run
python3 trigger_pipeline.py --station kan-bet --hours 2              # עיבוד תחנה
python3 trigger_pipeline.py                                          # כל התחנות
```

### 4.4 הגדרת Cron
```bash
(crontab -l 2>/dev/null; echo "0 * * * * cd /home/ubuntu/app/pipeline && python3 trigger_pipeline.py --station kan-bet --hours 2 >> /home/ubuntu/app/pipeline/cron.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "5 * * * * cd /home/ubuntu/app/pipeline && python3 trigger_pipeline.py --station glz --hours 2 >> /home/ubuntu/app/pipeline/cron.log 2>&1") | crontab -
```

---

## הערות חשובות

### EC2 = שרת Production
ה-deployment נעשה על אותו EC2 שרץ production. זה סותר את הדרישה המקורית של "הפרדה מוחלטת" — אבל ההחלטה נלקחה במודע. Services רצים ב-Docker ולא מפריעים ל-production הקיים.

### Dependencies על EC2
```bash
sudo apt install -y python3-pip
pip3 install boto3 requests
```
הערה: גרסת pip ישנה — לא תומכת ב---break-system-packages flag. להשתמש ב-pip3 install בלי הflag.

### Supabase
- Project כבר קיים עם סכמה מלאה, ~3,155 media records, ~24K segments indexed
- לא נדרש setup נוסף של DB

### עלויות חודשיות משוערות
| רכיב | עלות |
|--------|-------|
| RunPod (2 תחנות, ~720 שעות הקלטה) | $15-25 |
| EC2 (קיים) | $0 |
| Supabase (כבר קיים) | $0 |

---

## משימות שנותרו

1. אימות RunPod diarization — הרצת pipeline אמיתי אחרי אישור המודלים
2. הגדרת cron — שעתי ל-kan-bet ו-glz
3. DNS — הפניית דומיין ל-EC2 לגישה חיצונית ב-HTTPS
4. אינטגרציה ל-frontend — חיבור ה-API לממשק React הקיים של HaShomer
