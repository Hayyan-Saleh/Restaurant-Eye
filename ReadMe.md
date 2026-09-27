# Restaurant Eye — Multi-Camera Restaurant Monitoring System

A multi-camera monitoring system combining a Python computer-vision/KBS backend with a React frontend for live viewing.

## Architecture

- **15-camera MJPEG-over-HTTP** video streaming
- **AI Service (Python)** — YOLO-Pose + DeepSORT + Knowledge-Based System (Experta), running at 3fps
- **Frontend (React)** — live camera views with role/action overlays

## Backend (AI Service)

### Features

- Person detection & pose estimation (YOLO-Pose)
- Multi-object tracking (DeepSORT)
- Worker Re-ID via OSNet embeddings + FAISS
- Role & action classification via Experta-based KBS (ActionKBS, WorkerKBS, TableKBS)
- Zone-based classification (table / walk / work zones)
- Table state tracking (free / occupied / dirty)

### Setup

```bash
cd backend
pip install -r requirements.txt
python run_live.py
```

### Key modules

| File               | Purpose                                          |
| ------------------ | ------------------------------------------------ |
| `live_pipeline.py` | Main per-frame processing pipeline               |
| `run_live.py`      | Entry point, reads camera FPS, launches pipeline |
| `zone_manager.py`  | Zone definitions & classification                |
| `role_engine.py`   | Role/action KBS engine                           |
| `reid_gallery.py`  | Worker Re-ID via embeddings                      |
| `table_kbs.py`     | Table occupancy state machine                    |

### Requirements

- Python 3.10+
- Redis 7 (via Docker)
- GPU recommended 

## Frontend

### Setup

```bash
cd frontend
npm install
npm run dev
```

### Stack

- React
- Tailwind CSS
- Displays MJPEG camera streams with live bbox overlays (color-coded by role)

# 
