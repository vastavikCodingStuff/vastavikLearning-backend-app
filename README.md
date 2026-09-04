# Vastavik Learning Platform — Master Backend Engine (FastAPI)

Production-grade, ultra-lightweight backend engineered for the **Vastavik Learning** Android & iOS applications. Tailored for deployment on low-resource environments (2 vCPU, 2–4 GB RAM Linux VPS) with high concurrency, sub-second latency, zero-Redis in-memory caching/rate limiting, and dual-layer security.

---

## Architecture Highlights

- **Framework**: Python 3.11+ / FastAPI with `asyncio` non-blocking event loop.
- **Memory Footprint**: ~45–65 MB steady-state RAM (well within 2GB/4GB VPS budget).
- **Dual Protocol**: REST (HTTP/1.1 & HTTP/2 JSON) + WebSockets (Peer Chat & WebRTC Signaling).
- **Database**: Google Firebase Firestore (with resilient in-memory fallback for local dev & testing).
- **Dual-Layer Authentication**:
  - SHA-256 password hashing with 32-byte cryptographically secure per-user salt.
  - Constant-time verification (`hmac.compare_digest`) against timing attacks.
  - HMAC-SHA256 request signature verification with 5-minute replay attack window.
  - JWT Access (15m) and Refresh (7d) tokens with user & role claims.
- **Dynamic Route Circuit Breaker**:
  - Real-time in-memory `RouteStatusManager`.
  - Toggles routes (`ai_chat`, `code_execution`, `payments`, etc.) instantly without server restarts.
  - Fast-fails disabled routes with HTTP 503 and `Retry-After: 300`.
- **Fault-Tolerant Code Runner Proxy**:
  - Proxies Judge0 with 10-second fail-fast timeout to isolate external VPS downtime.
- **AI Tutoring Engine**:
  - Automatic fallback hierarchy: Mistral Small -> Google Gemini 3.7 Flash -> Google Gemini 3.6 Flash -> Local Tutor Fallback.
  - Server-Sent Events (SSE) word-by-word token streaming (`/api/v1/ai/chat/stream`).

---

## Directory Layout

```
vastavikLearning-backend-app/
├── app/
│   ├── main.py                     # Entry point, CORS, HMAC middleware, circuit breaker
│   ├── core/
│   │   ├── config.py               # Pydantic Settings and environment validation
│   │   ├── security.py             # SHA-256 + salt, JWT, and HMAC verification
│   │   ├── circuit_breaker.py      # RouteStatusManager & Circuit Breaker middleware
│   │   └── rate_limiter.py         # In-memory Token-Bucket rate limiter
│   ├── db/
│   │   └── firebase.py             # Firebase Admin SDK & resilient fallback repository
│   ├── models/
│   │   └── schemas.py              # Pydantic request/response schemas
│   └── routers/
│       ├── auth.py                 # Signup, login, refresh, Google/GitHub OAuth, device verify
│       ├── catalog.py              # Home catalog (5-min RAM cache), curriculum, lessons
│       ├── ai.py                   # AI chat proxy with Mistral/Gemini fallback & SSE stream
│       ├── code.py                 # Judge0 proxy (10s fail-fast timeout) & OCR cleaner
│       ├── realtime.py             # WebSockets (/socket.io/ peer chat & WebRTC signaling)
│       ├── doubts.py               # Student doubt tickets with multipart attachments
│       ├── payments.py             # Orders, gateway webhooks, and Pro unlocks
│       ├── notes.py                # Student revision notes CRUD
│       ├── pyq.py                  # Past Year Questions filter (ICSE / CBSE)
│       ├── search.py               # Global catalog search
│       ├── system.py               # Bug reports, GitHub cached app update, notifications
│       └── admin.py                # Runtime circuit breaker route toggles
├── nginx/
│   └── vastavik.conf               # NGINX reverse proxy with HTTP/2 & WebSockets
├── systemd/
│   └── vastavik-backend.service    # Linux systemd daemon configuration
├── tests/
│   └── test_backend.py             # 14 automated unit and integration tests
├── Dockerfile                      # Container build
├── docker-compose.yml              # Container orchestration
├── deploy.sh                       # Ubuntu VPS bootstrap & security hardening script
├── requirements.txt                # Lean dependencies
└── .env.example                    # Environment template
```

---

## API Endpoints Overview

| Category | Method & Route | Description |
| :--- | :--- | :--- |
| **Auth** | `POST /api/v1/auth/signup` | Register student with SHA-256 salted password |
| **Auth** | `POST /api/v1/auth/login` | Authenticate and issue access + refresh JWTs |
| **Auth** | `POST /api/v1/auth/refresh` | Issue new access token using refresh token |
| **Auth** | `POST /api/v1/auth/oauth/google` | Verify Google ID token & provision profile |
| **Auth** | `POST /api/v1/auth/oauth/github` | Exchange GitHub OAuth code & link user |
| **Auth** | `POST /api/v1/auth/device-verify` | Validate device integrity and root status |
| **User** | `GET /api/v1/user/profile` | Retrieve student profile and progress |
| **Catalog** | `GET /api/v1/catalog/home` | Cached home bundle (courses, banners, topics) |
| **Catalog** | `GET /api/v1/courses/{id}/curriculum` | Course parts, subparts, and syllabus |
| **Catalog** | `GET /api/v1/lessons/{id}` | Lesson details (checks Pro subscription) |
| **Catalog** | `POST /api/v1/progress/visited` | Mark curriculum part completed |
| **AI Engine** | `POST /api/v1/ai/chat` | AI query with Mistral/Gemini fallback |
| **AI Engine** | `GET /api/v1/ai/chat/stream` | SSE word-by-word streaming |
| **Code** | `POST /api/v1/code/execute` | Judge0 proxy (10s timeout, memory bound) |
| **Code** | `POST /api/v1/code/clean-ocr` | Normalize OCR typographical scan errors |
| **Realtime** | `WS /socket.io/` | Peer discussion room WebSocket |
| **Realtime** | `WS /ws/peer-chat` | Direct peer chat WebSocket |
| **Realtime** | `WS /ws/signaling/{class_id}` | WebRTC SDP/ICE candidate relay bus |
| **Doubts** | `POST /api/v1/doubts/submit` | Multipart doubt ticket with image/photo |
| **Payments** | `POST /api/v1/payments/create-order` | Generate order checksum and checkout token |
| **Payments** | `POST /api/v1/payments/webhook` | Gateway webhook verifying and unlocking Pro |
| **Payments** | `GET /api/v1/payments/history` | Student transaction and invoice history |
| **Notes** | `GET /api/v1/notes` | List student revision notes |
| **Notes** | `POST /api/v1/notes` | Create revision note |
| **Notes** | `DELETE /api/v1/notes/{id}` | Delete revision note |
| **PYQs** | `GET /api/v1/pyqs` | Filter past board exam questions |
| **Search** | `GET /api/v1/search` | Search courses, topics, and lessons |
| **System** | `POST /api/v1/system/bug-report` | Multipart bug report with diagnostics |
| **System** | `GET /api/v1/system/app-update` | Cached GitHub release information |
| **System** | `GET /api/v1/notifications` | Student notification inbox |
| **System** | `POST /api/v1/notifications/token` | Register FCM push device token |
| **Admin** | `GET /admin/routes` | View all route statuses (`ONLINE` / `OFFLINE`) |
| **Admin** | `POST /admin/routes/{feature}/toggle`| Toggle circuit breaker at runtime |
| **Health** | `GET /health` | Health monitoring & uptime endpoint |

---

## Quick Start (Local Development)

### 1. Clone and Setup Environment
```bash
git clone https://github.com/vastaviklearning/vastavikLearning-backend-app.git
cd vastavikLearning-backend-app

# Create virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables
```bash
cp .env.example .env
# Edit .env with your keys if desired (or run with defaults)
```

### 3. Run Development Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive Swagger Documentation: `http://localhost:8000/docs`  
Alternative ReDoc Documentation: `http://localhost:8000/redoc`

---

## Running Automated Tests

Run the complete test suite:
```bash
python -m pytest tests/test_backend.py -v
```

All 14 tests verify:
- SHA-256 salted password hashing & verification
- Constant-time comparison
- Dual-layer HMAC request signature verification & replay rejection
- JWT creation, decoding, and role claim checks
- Dynamic Route Circuit Breaker toggling & HTTP 503 fast-fail
- In-memory token bucket rate limiting
- OCR code cleaner
- Past Year Questions filtering
- Admin route toggles
- Notes CRUD lifecycle
- Payment order creation & webhook Pro subscription unlock
- Global catalog search & cached app update engine
- WebSockets peer chat messaging & WebRTC live signaling
- Judge0 fail-fast resilience

---

## Deployment to Railway & Render

This repository is pre-configured with native manifests for one-click or automated Git-push deployment to **Railway** and **Render**:

### Deploying to Render
1. Connect this GitHub repository in your [Render Dashboard](https://dashboard.render.com).
2. Render detects [`render.yaml`](file:///e:/vastavikCodingStuff/vastavikLearning-backend-app/render.yaml) automatically and provisions the web service.
3. Alternatively, create a new **Web Service** with:
   - **Environment**: Python
   - **Build Command**: `pip install --no-cache-dir -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --proxy-headers --forwarded-allow-ips="*"`
   - **Health Check Path**: `/health`

### Deploying to Railway
1. Open your [Railway Dashboard](https://railway.app) and select **New Project > Deploy from GitHub repo**.
2. Select this repository. Railway automatically reads [`railway.json`](file:///e:/vastavikCodingStuff/vastavikLearning-backend-app/railway.json) and [`Procfile`](file:///e:/vastavikCodingStuff/vastavikLearning-backend-app/Procfile).
3. The dynamic `$PORT` is bound automatically with health checks at `/health`.

---

## Deployment to Ubuntu Linux VPS

For complete setup on Ubuntu 22.04 or 24.04 LTS:

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

The deployment script automatically:
1. Installs Python 3, NGINX, UFW, and Fail2ban.
2. Hardens firewall (allows only ports 22, 80, 443).
3. Configures Fail2ban rate-limiting jails.
4. Sets up application in `/var/www/vastavik`.
5. Deploys NGINX reverse proxy with HTTP/2 and WebSocket pass-through.
6. Installs and starts the `vastavik-backend` systemd service with bounded memory limits.

---

## License

This project is licensed under the Apache-2.0 License.
