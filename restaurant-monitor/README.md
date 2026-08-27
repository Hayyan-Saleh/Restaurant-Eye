# Restaurant Monitor - Backend API

Real-time restaurant floor monitoring system with AI-powered person detection, zone management, and analytics.

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- PostgreSQL 14+
- Redis 6.x.x or later (Memurai on Windows)
- Node.js 18+ (for frontend)

### Installation & Setup

```bash
# Clone repository
git clone <repository-url>
cd restaurant-monitor

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your configuration
```

### Database Setup

**IMPORTANT:** Run Alembic from **project root** (not from backend/):

```bash
# From project root directory
alembic upgrade head
```

### Create Admin User

```bash
# From project root directory
python -m backend.app.seed.create_admin
```

**Alternative method (if above doesn't work):**
```bash
# From project root directory
cd backend
python -m app.seed.create_admin
cd ..
```

### Start Server

```bash
# From project root directory
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API available at: `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs`

## 📚 Documentation

**For Frontend Developers:** 👉 **[API_DOCUMENTATION.md](API_DOCUMENTATION.md)** - Complete API integration guide with code examples

**For Backend Developers:**
- `docs/ARCHITECTURE.md` - System architecture
- `docs/DB_SCHEMA.md` - Database schema
- `docs/EVENT_ENGINE.md` - Event engine details

## 🔧 Key Features

### Authentication & Security
- JWT Bearer token authentication
- Password hashing with Argon2
- Token revocation via Redis
- OTP-based password reset
- Email notifications

### Zone Management
- Create, update, delete zones
- Flexible zone type normalization
- Reference image snapshots
- Hierarchical zone structure
- AI pipeline compatibility

### System Settings
- Configurable worker idle limits
- Real-time settings updates
- Validation and constraints
- Reset to defaults

### Alerts Management
- Real-time alert monitoring
- Status filtering (ACTIVE/RESOLVED)
- Alert resolution tracking
- Camera and zone association

### Live Status Monitoring
- Real-time table status via Redis
- Worker status tracking
- Zone occupancy monitoring
- Customer session management

### WebSocket Support
- Real-time dashboard updates
- Authentication via token
- Connection management
- Event broadcasting

## 📡 API Endpoints Overview

### Authentication
- `POST /api/v1/auth/login` - Admin login
- `GET /api/v1/auth/me` - Get current admin
- `POST /api/v1/auth/logout` - Logout
- `POST /api/v1/auth/password/request-otp` - Request password reset
- `POST /api/v1/auth/password/reset` - Reset password

### Zones
- `GET /api/v1/zones/` - List zones
- `POST /api/v1/zones/` - Create zone
- `GET /api/v1/zones/{zone_id}` - Get zone details
- `PUT /api/v1/zones/{zone_id}` - Update zone
- `DELETE /api/v1/zones/{zone_id}` - Delete zone
- `GET /api/v1/zones/cameras/{camera_id}/snapshot` - Get reference image

### Settings
- `GET /api/v1/settings/` - Get settings
- `GET /api/v1/settings/detail` - Get detailed settings
- `PUT /api/v1/settings/` - Update settings
- `POST /api/v1/settings/reset` - Reset to defaults

### Alerts
- `GET /api/v1/alerts/` - List alerts
- `POST /api/v1/alerts/{alert_id}/resolve` - Resolve alert

### Live Status
- `GET /api/v1/live-status/tables/status` - Tables status
- `GET /api/v1/live-status/workers/status` - Workers status
- `GET /api/v1/live-status/zones-occupancy` - Zone occupancy
- `GET /api/v1/live-status/customer-sessions` - Customer sessions

## ⚠️ Important Notes for Frontend Developers

### Error Response Format
**Validation errors (HTTP 422) return an array of objects, not a string.** Handle this properly to prevent crashes.

### Zone Type Normalization
The system automatically normalizes zone types for AI pipeline compatibility:
- `table`, `dining`, `seating` → `table`
- `work`, `staff_zone`, `staff_area` → `work`
- `walk`, `corridor`, `pathway` → `walk`

### Authentication
- Include `Authorization: Bearer <token>` header for protected endpoints
- Tokens expire after 30 minutes
- Implement token refresh logic in your frontend

**👉 See [API_DOCUMENTATION.md](API_DOCUMENTATION.md) for complete integration guide with code examples.**

## 🏗️ Project Structure

```
restaurant-monitor/
├── alembic/                    # Database migrations
├── alembic.ini                 # Alembic configuration
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── api/               # API endpoints
│   │   │   └── v1/
│   │   │       ├── endpoints/ # Individual endpoint files
│   │   │       └── router.py  # API router
│   │   ├── core/              # Core configuration
│   │   │   ├── config.py      # Settings
│   │   │   ├── database.py    # Database setup
│   │   │   ├── redis.py       # Redis setup
│   │   │   └── security.py    # Authentication
│   │   ├── models/            # Database models
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── services/          # Business logic
│   │   ├── websockets/        # WebSocket handlers
│   │   ├── seed/              # Database seeding scripts
│   │   └── main.py            # Application entry
│   └── requirements.txt       # Python dependencies
├── src/                       # AI Pipeline
├── storage/                   # Storage utilities
├── docs/                      # Technical documentation
├── .env                       # Environment variables
├── .env.example               # Environment template
├── requirements.txt           # Project dependencies
├── README.md                  # This file (Quick start & overview)
└── API_DOCUMENTATION.md       # Complete API guide for frontend
```

## 🔒 Security

- JWT tokens with configurable expiration
- Password hashing with Argon2
- Token revocation via Redis
- CORS configuration for development
- Input validation with Pydantic
- SQL injection prevention via SQLAlchemy

## 🐛 Troubleshooting

### Database Connection
```bash
# Check PostgreSQL service
# On Windows: Check Services for "postgresql-x64-14"

# Test connection
psql -U postgres -d restaurant_db
```

### Redis Connection
```bash
# On Windows with Memurai
# Check Memurai service in Services

# Test connection
redis-cli ping

# Check Redis version (must be 6.x.x or later)
redis-cli --version
```

### Alembic Issues
**Always run from project root:**
```bash
# ✅ CORRECT
alembic upgrade head

# ❌ INCORRECT
cd backend
alembic upgrade head
```

### Missing Dependencies
```bash
pip install -r requirements.txt --force-reinstall
```

## 📝 Environment Variables

See `.env.example` for all required variables:

- `DATABASE_URL` - PostgreSQL async connection
- `SYNC_DATABASE_URL` - PostgreSQL sync connection (for Alembic)
- `JWT_SECRET_KEY` - Minimum 32 characters
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` - Email configuration
- `REDIS_HOST`, `REDIS_PORT` - Redis configuration
- `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` - Default admin credentials

## 🤝 Contributing

1. Follow the existing code structure
2. Use async/await for database operations
3. Add proper error handling
4. Update documentation for new features
5. Test thoroughly before committing

## 📄 License

[Your License Here]

## 📞 Support

For issues or questions:
- **Frontend:** See [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- **Backend:** Check technical documentation in `docs/`
- Review Swagger UI at `/docs`
- Check the troubleshooting section