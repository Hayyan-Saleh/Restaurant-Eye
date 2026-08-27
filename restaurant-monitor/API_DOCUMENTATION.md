# Restaurant Monitor API Documentation

Complete API integration guide for frontend developers with comprehensive endpoint reference and code examples.

## 📡 Base URL

**Development:** `http://localhost:8000/api/v1`
**Production:** `<your-production-url>/api/v1`

**Interactive Documentation:** `http://localhost:8000/docs` (Swagger UI)

---

## 🔐 Authentication Flow

### Overview
All endpoints require authentication using JWT Bearer tokens.

## 🎯 Admin User Creation

### Create Initial Admin User
Before using the API, create the initial admin user:

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

This creates a default admin account with credentials from your `.env` file:
- Email: `INITIAL_ADMIN_EMAIL` (default: admin@restaurant.com)
- Password: `INITIAL_ADMIN_PASSWORD` (default: admin123)

**Important:** Change the password after first login.

### 1. Login & Get Token
```typescript
// POST /api/v1/auth/login
const login = async (email: string, password: string) => {
  const response = await fetch('http://localhost:8000/api/v1/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email, password }),
  });

  const data = await response.json();

  if (response.ok) {
    // Store token for future requests
    localStorage.setItem('token', data.access_token);
    return data;
  } else {
    throw new Error(data.detail);
  }
};
```

**Request:**
```json
{
  "email": "admin@restaurant.com",
  "password": "admin123"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 2. Use Token in Requests
```typescript
// Add Bearer token to all protected endpoints
const authenticatedFetch = async (url: string, options = {}) => {
  const token = localStorage.getItem('token');

  const response = await fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  });

  return response.json();
};
```

### 3. Logout (Revoke Token)
```typescript
// POST /api/v1/auth/logout
const logout = async () => {
  await authenticatedFetch('http://localhost:8000/api/v1/auth/logout', {
    method: 'POST',
  });
  localStorage.removeItem('token');
};
```

### Token Expiration
- Tokens expire after 30 minutes
- Implement token refresh logic in your frontend
- Check expiration before making requests:

```typescript
const checkTokenExpiration = () => {
  const token = localStorage.getItem('token');
  if (!token) return false;

  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const exp = payload.exp * 1000; // Convert to milliseconds
    return Date.now() < exp;
  } catch {
    return false;
  }
};

// Use before authenticated requests
if (!checkTokenExpiration()) {
  // Redirect to login
  window.location.href = '/login';
}
```

---

## ⚠️ Error Response Handling (CRITICAL)

### **IMPORTANT:** Error Response Format

**Validation errors (HTTP 422) return an array of objects, not a string.** Handle this properly to prevent app crashes.

### Error Response Types

**Validation Error (422):**
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    },
    {
      "loc": ["body", "password"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

**Authentication Error (401):**
```json
{
  "detail": "Could not validate credentials"
}
```

**Not Found Error (404):**
```json
{
  "detail": "Resource not found"
}
```

### Proper Error Handling Code

```typescript
// ❌ INCORRECT - This will crash your app
const handleError = (error: any) => {
  alert(error.detail); // error.detail is an array, not a string!
};

// ✅ CORRECT - Handle array of validation errors
const handleError = (error: any) => {
  if (error.detail && Array.isArray(error.detail)) {
    // Handle validation errors (422)
    const messages = error.detail.map((err: any) => err.msg).join(', ');
    alert(`Validation Error: ${messages}`);
  } else if (error.detail && typeof error.detail === 'string') {
    // Handle other errors (401, 404, etc.)
    alert(error.detail);
  } else {
    alert('An unknown error occurred');
  }
};
```

---

## 🎯 Complete API Client Implementation

### Recommended ApiClient Class

```typescript
class ApiClient {
  private baseUrl = 'http://localhost:8000/api/v1';

  private getHeaders() {
    const token = localStorage.getItem('token');
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    };
  }

  private async handleResponse(response: Response) {
    const data = await response.json();

    if (!response.ok) {
      if (Array.isArray(data.detail)) {
        const messages = data.detail.map((err: any) => err.msg).join(', ');
        throw new Error(`Validation Error: ${messages}`);
      }
      throw new Error(data.detail || 'Request failed');
    }

    return data;
  }

  async get(endpoint: string) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }

  async post(endpoint: string, body: any) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify(body),
    });
    return this.handleResponse(response);
  }

  async put(endpoint: string, body: any) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      method: 'PUT',
      headers: this.getHeaders(),
      body: JSON.stringify(body),
    });
    return this.handleResponse(response);
  }

  async delete(endpoint: string) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      method: 'DELETE',
      headers: this.getHeaders(),
    });
    return this.handleResponse(response);
  }
}

// Usage Example
const api = new ApiClient();

// Login
const loginData = await api.post('/auth/login', { 
  email: 'admin@restaurant.com', 
  password: 'admin123' 
});
localStorage.setItem('token', loginData.access_token);

// Get zones
const zones = await api.get('/zones/');

// Create zone
const newZone = await api.post('/zones/', {
  name: 'Table 1',
  camera_id: 'camera_01',
  zone_type: 'table',
  polygon_coordinates: {
    points: [[0, 0], [100, 0], [100, 100], [0, 100]],
    image_size: { width: 1920, height: 1080 }
  }
});
```

---

## 📚 API Endpoints Reference

### Authentication Endpoints

#### POST `/api/v1/auth/login`
Authenticate and receive access token.

**Request Body:**
```json
{
  "email": "string",
  "password": "string"
}
```

**Response:**
```json
{
  "access_token": "string"
}
```

**Error Responses:**
- `401`: Invalid email or password
- `422`: Validation error (array of objects)

---

#### GET `/api/v1/auth/me`
Get current admin information.

**Response:**
```json
{
  "id": "uuid",
  "email": "string"
}
```

**Error Responses:**
- `401`: Invalid or missing token

---

#### POST `/api/v1/auth/logout`
Logout and revoke current token.

**Error Responses:**
- `401`: Invalid or expired token
- `422`: Invalid authorization header format

---

#### POST `/api/v1/auth/password/request-otp`
Request OTP for password reset.

**Request Body:**
```json
{
  "email": "string"
}
```

**Response:**
```json
{
  "message": "OTP code has been generated and sent successfully."
}
```

**Development Mode (DEV_MODE=true):**
For local testing, set `DEV_MODE=true` in your `.env` file. The OTP will be included in the response message:
```json
{
  "message": "OTP code has been generated and sent successfully. DEV MODE: OTP is 123456"
}
```

**Error Responses:**
- `404`: Email address not found
- `422`: Validation error (array of objects)

---

#### POST `/api/v1/auth/password/reset`
Reset password using OTP.

**Request Body:**
```json
{
  "email": "string",
  "otp": "string",
  "new_password": "string"
}
```

**Response:**
```json
{
  "message": "Password changed successfully"
}
```

**Error Responses:**
- `400`: Invalid OTP or expired
- `422`: Validation error (array of objects)

---

### Zones Management Endpoints
## 🎯 Admin User Creation

### Insert the zones you have in your project into database
Before using the API, create the initial admin user:

```bash
# From project root directory
python -m backend.app.seed.seed_cameras
python -m backend.app.seed.seed_zones

```


#### Zone Type Normalization
The system automatically normalizes zone types for AI pipeline compatibility:

| Input Type | Normalized Type | Description |
|------------|-----------------|-------------|
| `customer_zone`, `table_area`, `dining`, `seating` | `table` | Customer table areas |
| `staff_zone`, `staff_area`, `kitchen_area`, `buffet`, `cashier` | `work` | Staff working areas |
| `walkway`, `service_path`, `hallway`, `corridor`, `entrance`, `entry_exit` | `walk` | Walking paths |

**Important:** The system preserves the original value chosen by the admin in the `original_zone_type` field, while storing the normalized value in `zone_type` for AI pipeline compatibility.

---

#### GET `/api/v1/zones/cameras/{camera_id}/snapshot`
Get camera reference image for zone mapping.

**Parameters:**
- `camera_id` (path): Camera identifier

**Response:** Image file (JPEG/PNG)

**Error Responses:**
- `404`: Image not found for specified camera

---

#### GET `/api/v1/zones/cameras/{camera_id}`
Retrieve all zones for a specific camera.

**Parameters:**
- `camera_id` (path): Camera identifier

**Response:**
```json
[
  {
    "id": "uuid",
    "camera_id": "string",
    "parent_zone_id": "uuid|null",
    "name": "string",
    "zone_type": "string",
    "original_zone_type": "string|null",
    "polygon_coordinates": {
      "points": [[x, y], ...],
      "image_size": {"width": int, "height": int}
    },
    "auto_generated": false,
    "excludes_tables": false,
    "created_at": "datetime",
    "updated_at": "datetime"
  }
]
```

**Error Responses:**
- `404`: Camera not found

---

#### POST `/api/v1/zones/`
Create a new zone.

**Request Body:**
```json
{
  "camera_id": "string",
  "name": "string",
  "zone_type": "string",
  "polygon_coordinates": {
    "points": [[x, y], ...],
    "image_size": {"width": int, "height": int}
  },
  "parent_zone_id": "uuid|null",
  "excludes_tables": false,
  "auto_generated": false
}
```

**Response:** Zone object (201 Created)

**Important:** The system automatically normalizes the `zone_type` for AI pipeline compatibility and stores the original value in `original_zone_type`.

**Error Responses:**
- `400`: Invalid request payload
- `404`: Camera or parent zone not found
- `422`: Validation error (array of objects)

---

#### PUT `/api/v1/zones/{zone_id}`
Update an existing zone.

**Parameters:**
- `zone_id` (path): Zone identifier

**Request Body:**
```json
{
  "name": "string",
  "zone_type": "string",
  "polygon_coordinates": {
    "points": [[x, y], ...],
    "image_size": {"width": int, "height": int}
  },
  "parent_zone_id": "uuid|null",
  "excludes_tables": false,
  "auto_generated": false
}
```

**Response:** Updated zone object

**Error Responses:**
- `400`: Invalid payload format
- `404`: Zone or parent zone not found
- `422`: Validation error (array of objects)

---

#### DELETE `/api/v1/zones/{zone_id}`
Delete a zone.

**Parameters:**
- `zone_id` (path): Zone identifier

**Response:** 204 No Content

**Error Responses:**
- `404`: Zone not found

---

### System Settings Endpoints

#### Settings Validation
- **Minimum Value**: 10 seconds
- **Maximum Value**: 3600 seconds (60 minutes)
- **Recommended Values**: 30, 60, 90, 120, 180, 300, 600 seconds

---

#### GET `/api/v1/settings/`
Get current system settings.

**Response:**
```json
{
  "id": 1,
  "worker_idle_limit": 60,
  "updated_at": "datetime"
}
```

**Error Responses:**
- `404`: Settings not found (auto-created)

---

#### GET `/api/v1/settings/detail`
Get detailed system settings with formatted time values.

**Response:**
```json
{
  "id": 1,
  "worker_idle_limit": 60,
  "updated_at": "datetime",
  "worker_idle_limit_minutes": 1.0,
  "worker_idle_limit_formatted": "1 دقيقة"
}
```

**Error Responses:**
- `404`: Settings not found (auto-created)

---

#### PUT `/api/v1/settings/`
Update system settings.

**Request Body:**
```json
{
  "worker_idle_limit": 120
}
```

**Response:** Updated settings object

**Error Responses:**
- `400`: Invalid data (value out of range)
- `404`: Settings not found (auto-created)
- `422`: Validation error (array of objects)

---

#### POST `/api/v1/settings/reset`
Reset settings to default values.

**Response:** Reset settings object (idle limit = 60 seconds)

**Error Responses:**
- `404`: Settings not found (auto-created)

---

### Alerts Management Endpoints

#### GET `/api/v1/alerts/`
Retrieve alerts with optional status filtering.

**Query Parameters:**
- `status` (optional): Filter by alert status (`ACTIVE`, `RESOLVED`)

**Response:**
```json
{
  "alerts": [
    {
      "id": 1,
      "alert_type": "string",
      "entity_id": "string|null",
      "camera_id": "string|null",
      "zone_id": "string|null",
      "message": "string",
      "status": "ACTIVE|RESOLVED",
      "created_at": "datetime",
      "resolved_at": "datetime|null"
    }
  ]
}
```

**Default Behavior:** Returns active alerts if no status is specified.

**Error Responses:**
- `401`: Invalid or missing token

---

#### POST `/api/v1/alerts/{alert_id}/resolve`
Mark an alert as resolved.

**Parameters:**
- `alert_id` (path): Alert identifier

**Response:**
```json
{
  "id": 1,
  "status": "RESOLVED",
  "resolved_at": "datetime"
}
```

**Error Responses:**
- `404`: Alert not found
- `400`: Alert already resolved

---

### Live Status Monitoring Endpoints

#### GET `/api/v1/live-status/tables/status`
Get real-time status of all tables from Redis.

**Response:**
```json
{
  "tables": [
    {
      "zone_id": "string",
      "camera_id": "string",
      "status": "string"
    }
  ]
}
```

**Error Responses:**
- `401`: Invalid or missing token

---

#### GET `/api/v1/live-status/workers/status`
Get real-time status of all workers from Redis.

**Response:**
```json
{
  "workers": [
    {
      "entity_id": "string",
      "camera_id": "string|null",
      "zone_id": "string|null",
      "status": "string"
    }
  ]
}
```

**Error Responses:**
- `401`: Invalid or missing token

---

#### GET `/api/v1/live-status/zones-occupancy`
Get real-time occupancy count for all zones from Redis.

**Response:**
```json
{
  "zones": [
    {
      "camera_id": "string",
      "zone_id": "string",
      "zone_name": "string",
      "count": 0
    }
  ]
}
```

**Error Responses:**
- `401`: Invalid or missing token

---

#### GET `/api/v1/live-status/customer-sessions`
Get customer sessions with optional status filtering.

**Query Parameters:**
- `status` (optional): Filter by session status

**Response:**
```json
{
  "customer_sessions": [
    {
      "id": "uuid",
      "entity_id": "string",
      "table_zone_id": "string",
      "camera_id": "string",
      "started_at": "datetime",
      "last_seen_at": "datetime",
      "left_at": "datetime|null",
      "total_stay_sec": "int",
      "status": "ACTIVE|COMPLETED"
    }
  ]
}
```

**Error Responses:**
- `401`: Invalid or missing token

---

## 🔄 Real-time Updates (WebSocket)

### WebSocket Connection
```typescript
// Connect to WebSocket for real-time updates
const connectWebSocket = () => {
  const token = localStorage.getItem('token');
  const ws = new WebSocket(`ws://localhost:8000/ws/dashboard?token=${token}`);

  ws.onopen = () => {
    console.log('WebSocket connected');
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // Handle real-time updates
    console.log('Received update:', data);
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };

  ws.onclose = () => {
    console.log('WebSocket disconnected');
    // Implement reconnection logic
  };

  return ws;
};
```

### WebSocket Events
The WebSocket sends real-time updates for:
- Zone occupancy changes
- Worker status updates
- Table status changes
- New alerts
- System setting changes

---


---

## 🚨 Troubleshooting

### CORS Issues
If you encounter CORS errors:
1. Ensure the backend is running
2. Check that CORS is configured in `backend/app/main.py`
3. Verify you're using the correct base URL

### Network Errors
Implement proper error handling and retry logic:

```typescript
const fetchWithRetry = async (url: string, options: any, retries = 3) => {
  for (let i = 0; i < retries; i++) {
    try {
      const response = await fetch(url, options);
      if (response.ok) return response.json();
      throw new Error('Request failed');
    } catch (error) {
      if (i === retries - 1) throw error;
      await new Promise(resolve => setTimeout(resolve, 1000 * (i + 1)));
    }
  }
};
```

### Token Issues
- Always check token expiration before requests
- Implement automatic token refresh if needed
- Handle 401 errors by redirecting to login
- Clear token on logout

### Validation Errors
- Always handle 422 errors as arrays of objects
- Display user-friendly error messages
- Validate form data before sending to API

---

## 📊 Quick Reference

### HTTP Status Codes
- `200 OK`: Successful GET/PUT/DELETE
- `201 Created`: Successful POST
- `400 Bad Request`: Invalid input data
- `401 Unauthorized`: Invalid or missing authentication
- `404 Not Found`: Resource not found
- `422 Unprocessable Entity`: Validation error (array of objects)
- `500 Internal Server Error`: Server error

### Common Request Headers
```typescript
{
  'Content-Type': 'application/json',
  'Authorization': 'Bearer <token>'
}
```

### Authentication Flow
1. POST `/api/v1/auth/login` → Get token
2. Store token in localStorage
3. Include `Authorization: Bearer <token>` in all requests
4. POST `/api/v1/auth/logout` → Revoke token

---

## 🔒 Security Notes

- JWT tokens expire after 30 minutes
- Always use HTTPS in production
- Never expose tokens in client-side code
- Implement proper CORS configuration
- Validate all user input
- Handle errors gracefully without exposing sensitive information

---

## 📞 Support

For API integration issues:
- Check Swagger UI at `http://localhost:8000/docs`
- Review error responses carefully (check if array or string)
- Ensure token is valid and not expired
- Verify CORS configuration
- Check browser console for detailed error messages

---

**Document Version:** 2.0  
**Last Updated:** 2026-08-06  
**API Version:** v1.0.0