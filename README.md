# 🌍 Geo Intelligence Platform

AI-powered geospatial intelligence system using **Google Earth Engine**, **BigQuery**, and **AI reasoning** to analyze environmental changes through satellite data (NDVI).

---

## 🚀 Overview

The Geo Intelligence Platform is a cloud-ready system that processes satellite imagery to generate environmental insights. It integrates:

- 🌍 Google Earth Engine (satellite processing)
- 🤖 AI reasoning layer (environmental interpretation)
- 📊 BigQuery (data storage & analytics)
- 🌐 REST API (Flask backend)
- 🧩 Multi-language architecture (Python + optional PHP gateway)

The system focuses on **vegetation health monitoring (NDVI analysis)** across Kenya.

---

## 🧠 Key Features

- NDVI computation from Sentinel-2 satellite data
- AI-based environmental classification
- REST API for real-time geospatial insights
- BigQuery integration for structured data storage
- Modular microservice architecture
- Cloud deployment ready (GCP / Cloud Run)

---

## 🏗️ System Architecture

```

Browser / Client
↓
FastAPI API (main.py)
↓
AI Insight Layer (core/gemini_client.py)
↓
Earth Engine Core (core/gee_client.py)
↓
BigQuery Pipeline (services/storage_service.py)
↓
Google Cloud Platform

```

Optional extension:
```

PHP API Gateway → Python GEE Engine

```

---

## 📦 Project Structure

```
geo-intelligence-platform/
│
├── gee-api/
│   ├── main.py              # FastAPI application entrypoint
│   ├── wsgi.py              # Gunicorn/WGSI entrypoint
│   ├── config.py            # Settings management
│   ├── core/                # async Earth Engine, Gemini, embeddings, Redis
│   ├── middleware/          # auth, logging, rate limiting
│   ├── routes/              # API routes
│   ├── services/            # domain service layer
│   ├── models/              # request/response schemas
│   ├── utils/               # shared helpers and response formatting
│   └── requirements.txt
│
├── deploy-gee-api/
│   ├── 00-orchestrator.sh
│   ├── 01-setup.sh
│   ├── 02-env-check.sh
│   ├── 03-install-deps.sh
│   ├── 04-auth-gcp.sh
│   └── 05-run-api.sh
│
├── gee-node-api/            # PHP gateway (optional)
│
└── README.md
```

---

## ⚙️ Installation

### 1. Clone repository

```bash
git clone https://github.com/YOUR_USERNAME/geo-intelligence-platform.git
cd geo-intelligence-platform
```

---

### 2. Setup environment

```bash
cd deploy-gee-api
chmod +x *.sh
./00-orchestrator.sh
```

---

### 3. Run API manually (optional)

```bash
cd gee-api
pip install -r requirements.txt
python main.py
```

---

## 🌐 API Endpoints

### 🔹 Health Check

```
GET /health
```

Response:

```json
{
  "status": "running",
  "service": "gee-capstone-api"
}
```

---

### 🔹 NDVI Analysis

```
GET /ndvi
```

Response:

```json
{
  "region": "Kenya",
  "ndvi": 0.42
}
```

---

### 🔹 AI Insight

```
GET /insight
```

Response:

```json
{
  "ndvi": 0.42,
  "insight": "Moderate vegetation health"
}
```

---

### 🔹 Store to BigQuery

```
GET /store
```

Stores NDVI results into BigQuery dataset.

---

## 🛰️ AI Logic

NDVI values are classified as:

* < 0.2 → Low vegetation (drought / urban expansion)
* 0.2 – 0.5 → Moderate vegetation health
* > 0.5 → High vegetation health

---

## ☁️ Cloud Deployment (Optional)

Supports deployment on:

* Google Cloud Run
* Compute Engine
* Kubernetes Engine

Example (Cloud Run):

```bash
gcloud run deploy geo-intelligence \
  --source . \
  --platform managed \
  --region us-central1
```

---

## ✅ Final Verification Checklist

### 1. All Critical Fixes Applied
- ✅ `DOCX_AVAILABLE` import added
- ✅ `embedding_cache` initialized
- ✅ `safe_redis_op` function defined
- ✅ Single shutdown handler (duplicate removed)
- ✅ `wsgi.py` correctly imports from `main`

### 2. Deployment Scripts Ready
- ✅ `deploy-gee-api/08-cloud-run-deploy.sh`
- ✅ `gee-api/Dockerfile`
- ✅ `deploy-gee-api/05-run-api.sh`

### 3. Deployment Commands

#### Local Development Test
```bash
cd /workspaces/geo-intelligence-platform/gee-api

export GEMINI_API_KEY="your-test-key"
export JWT_SECRET="test-secret-minimum-32-chars"
export ENVIRONMENT="development"

python main.py
```

Then in another terminal:
```bash
curl http://localhost:8000/health
```

#### Production Deployment to Cloud Run
```bash
cd /workspaces/geo-intelligence-platform/deploy-gee-api
chmod +x *.sh
export GOOGLE_CLOUD_PROJECT="your-project-id"
export SERVICE_NAME="geo-intelligence-api"
export REGION="us-central1"
./00-orchestrator.sh
```

#### Quick Cloud Run Deployment
```bash
cd /workspaces/geo-intelligence-platform/deploy-gee-api
./08-cloud-run-deploy.sh
```

### 4. Required Secrets
```bash
gcloud secrets create gee-api-gemini-key --data-file=- <<< "your-gemini-api-key"
gcloud secrets create gee-api-jwt-secret --data-file=- <<< "your-jwt-secret-min-32-chars"
```

### 5. Environment Variables Summary

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | ✅ | - | Google Gemini API key |
| `JWT_SECRET` | ✅ | - | JWT signing secret (min 32 chars) |
| `GOOGLE_CLOUD_PROJECT` | ✅ | - | GCP Project ID |
| `ENVIRONMENT` | ❌ | production | Environment name |
| `LOG_LEVEL` | ❌ | INFO | Logging level |
| `REDIS_HOST` | ❌ | localhost | Redis host |
| `REDIS_PORT` | ❌ | 6379 | Redis port |
| `PORT` | ❌ | 8000 | Application port |

---

## 🏛️ Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                    Cloud Run (Production)                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              geo-intelligence-api                      │  │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐   │  │  │
│  │  │   Auth    │  │  Rate     │  │  Request  │   │  │  │
│  │  │Middleware │  │  Limit    │  │    ID     │   │  │  │
│  │  └───────────┘  └───────────┘  └───────────┘   │  │  │
│  │  ┌─────────────────────────────────────────┐   │  │  │
│  │  │         API Endpoints                    │   │  │  │
│  │  │  /api/ndvi  /api/change-detection       │   │  │  │
│  │  │  /api/ai/analyze  /api/documents/*      │   │  │  │
│  │  └─────────────────────────────────────────┘   │  │  │
│  │  ┌─────────────────────────────────────────┐   │  │  │
│  │  │           Service Layer                  │   │  │  │
│  │  │  GeospatialAnalysisService               │   │  │  │
│  │  │  DocumentIntelligenceService             │   │  │  │
│  │  └─────────────────────────────────────────┘   │  │  │
│  │  ┌─────────────────────────────────────────┐   │  │  │
│  │  │            External Clients              │   │  │  │
│  │  │  EarthEngine  │  Gemini AI  │  Redis    │   │  │  │
│  │  └─────────────────────────────────────────┘   │  │  │
│  │  └───────────────────────────────────────────────────────┘  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  Earth Engine │    │   Gemini AI   │    │    Redis      │
│   (GEE API)   │    │   (Google)    │    │ (Memorystore) │
└───────────────┘    └───────────────┘    └───────────────┘
```

---

## 🔐 Requirements

* Google Earth Engine account
* Google Cloud Project
* Python 3.9+
* gcloud CLI configured

---

## 📊 Use Cases

* Climate monitoring
* Drought detection
* Agricultural intelligence
* Environmental change tracking
* Research & policy support

---

## 🧪 Tech Stack

* Python (Flask)
* Google Earth Engine API
* BigQuery
* Google Cloud Platform
* PHP (optional gateway layer)
* Bash (deployment automation)

---

## 🏆 Project Status

✔ Satellite processing complete
✔ AI reasoning layer complete
✔ API layer complete
✔ Cloud-ready architecture
⏳ UI dashboard (optional future upgrade)

---

## 👨‍💻 Author

**Stephen Karitu**
Arybit Technologies
Kenya 🇰🇪

---

## 📜 License

MIT License (recommended for open research and academic use)

---

## 🚀 Future Improvements

* Add Gemini AI summarization layer
* Build interactive map dashboard (Leaflet / React)
* Real-time satellite streaming
* CI/CD pipeline (GitHub Actions)
* Multi-region scaling on GCP
