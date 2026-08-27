# Original Zone Type Preservation Fix

## 🎯 Problem Description

**Issue:** In `app/schemas/zone.py`, the `model_validator` function was directly modifying `self.zone_type` during validation, converting the original value (e.g., "customer_zone") to the normalized value (e.g., "table") for AI pipeline compatibility. This caused the original admin choice to be lost permanently.

**Impact:** Admins could not see what they originally selected, and there was no record of their original choice in the database.

---

## ✅ Solution Applied

### 1. Database Model Update (`backend/app/models/zone.py`)
**Change:** Added `original_zone_type` field to preserve the original admin choice.

```python
# Added new field
original_zone_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
```

**Rationale:**
- Nullable for backward compatibility with existing data
- Stores the original value before normalization
- Allows tracking what admins actually selected

---

### 2. Schema Updates (`backend/app/schemas/zone.py`)

#### A. ZoneCreate Schema
**Changes:**
- Added `original_zone_type` optional field
- Modified `normalize_zone_type_field` to preserve original value

```python
original_zone_type: Optional[str] = Field(None, description="القيمة الأصلية لنوع المنطقة قبل التطبيع")

@model_validator(mode='after')
def normalize_zone_type_field(self):
    if self.zone_type:
        # Preserve original value if not explicitly provided
        if not self.original_zone_type:
            self.original_zone_type = self.zone_type
        # Normalize for database
        self.zone_type = normalize_zone_type(self.zone_type)
    return self
```

#### B. ZoneUpdate Schema
**Changes:** Same modifications as ZoneCreate for consistency.

#### C. ZoneResponse Schema
**Changes:** Added `original_zone_type` field to response.

```python
original_zone_type: Optional[str]
```

---

### 3. Database Migration (`alembic/versions/add_original_zone_type_to_zones.py`)
**Created:** New migration file to add the `original_zone_type` column.

```python
def upgrade():
    op.add_column('zones', sa.Column('original_zone_type', sa.String(50), nullable=True))

def downgrade():
    op.drop_column('zones', 'original_zone_type')
```

**Rationale:**
- Uses Alembic for safe database schema changes
- Nullable column ensures existing data remains valid
- Provides rollback capability

---

### 4. Documentation Updates (`API_DOCUMENTATION.md`)
**Changes:**
- Updated zone response examples to include `original_zone_type`
- Added note about original value preservation
- Enhanced zone type normalization explanation

---

## 🔒 Backward Compatibility

### ✅ Ensured Compatibility:
1. **Nullable Field:** `original_zone_type` is nullable, so existing zones won't break
2. **Optional Input:** `original_zone_type` is optional in schemas
3. **Automatic Preservation:** System automatically saves original value if not provided
4. **Safe Migration:** Alembic migration allows rollback

### ✅ No Breaking Changes:
- Existing zones continue to work normally
- AI pipeline compatibility maintained
- Frontend can use either field as needed
- No changes required to existing zone data

---

## 📊 How It Works Now

### Example Flow:

**1. Admin Creates Zone:**
```json
POST /api/v1/zones/
{
  "zone_type": "customer_zone"  // Original choice
}
```

**2. System Processing:**
- Original value saved: `original_zone_type = "customer_zone"`
- Normalized value saved: `zone_type = "table"` (for AI pipeline)

**3. Database Storage:**
```sql
zone_type = "table"              // Normalized (AI uses this)
original_zone_type = "customer_zone"  // Original (admin sees this)
```

**4. Frontend Response:**
```json
{
  "zone_type": "table",              // For AI pipeline
  "original_zone_type": "customer_zone"  // What admin chose
}
```

---

## 🎯 Benefits

1. **Admin Choice Preservation:** Original admin selections are now saved and visible
2. **AI Pipeline Compatibility:** Normalized values still used for AI pipeline
3. **Audit Trail:** Can track what admins originally selected vs normalized values
4. **Flexibility:** Frontend can display either value based on needs
5. **Backward Compatible:** No impact on existing data or functionality

---

## 📋 Required Actions (For User)

### ⚠️ Important: Run Database Migration
Before the new feature will work, you must run the migration:

```bash
# From project root directory
alembic upgrade head
```

This will add the `original_zone_type` column to the `zones` table.

### Optional: Test the Feature
After migration, test creating a new zone:
```bash
# Start the server
cd backend
python -m uvicorn app.main:app --reload

# Create a zone with a non-standard type
POST /api/v1/zones/
{
  "camera_id": "camera_01",
  "name": "Test Zone",
  "zone_type": "customer_zone",
  "polygon_coordinates": {
    "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
    "image_size": {"width": 1920, "height": 1080}
  }
}
```

**Expected Response:**
```json
{
  "zone_type": "table",
  "original_zone_type": "customer_zone",
  ...
}
```

---

## 🔍 Verification Checklist

- ✅ Database model updated with `original_zone_type` field
- ✅ Schemas updated to preserve original value
- ✅ Migration file created for database schema change
- ✅ Documentation updated with new field information
- ✅ Backward compatibility maintained
- ⚠️ **User Action Required:** Run `alembic upgrade head` to apply migration

---

## 📞 Support

If you encounter any issues after running the migration:
1. Check that the migration was applied successfully
2. Verify the database schema has the new column
3. Check server logs for any errors
4. Test creating a new zone to verify the feature works

The fix is complete and ready for use once the migration is applied.