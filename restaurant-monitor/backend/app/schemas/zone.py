# DTOs المناطق والإحداثيات (Polygon JSONB)
from datetime import datetime
from typing import Optional, List, Union, Any
from pydantic import BaseModel, Field, field_validator, model_validator
from uuid import UUID


# خريطة تطبيع أنواع المناطق للتوافق مع AI Pipeline
# هذه الخريطة تحول الأنواع المستخدمة في الواجهة الأمامية إلى القيم الموحدة المتوقعة من الـ AI
ZONE_TYPE_NORMALIZATION_MAP = {
    # API Frontend → AI Pipeline Normalized Value
    "customer_zone": "table",       # مناطق العملاء/الطاولات
    "staff_zone": "work",           # مناطق الموظفين
    "walkway": "walk",              # الممرات
    "kitchen_area": "work",         # المطبخ
    "entry_exit": "walk",           # المداخل والمخارج
    
    # القيم القديمة للتوافق (من zones_config.json القديم)
    "table_area": "table",
    "staff_area": "work",
    "service_path": "walk",
    "hallway": "walk",
    "corridor": "walk",
    "open_area": "walk",
    "mixed_area": "walk",
    "entrance": "walk",
    "buffet": "work",
    "cashier": "work",
    
    # القيم الموحدة نفسها (لا تغيير)
    "table": "table",
    "walk": "walk",
    "work": "work",
    "unknown": "unknown",
}


def normalize_zone_type(zone_type: str) -> str:
    """
    تطبيع نوع المنطقة للتوافق مع AI Pipeline
    
    Args:
        zone_type: نوع المنطقة كما يدخله الأدمن
    
    Returns:
        القيمة الموحدة المتوقعة من الـ AI pipeline
    """
    return ZONE_TYPE_NORMALIZATION_MAP.get(zone_type, zone_type)


class PolygonCoordinates(BaseModel):
    """نموذج مرن لتمثيل إحداثيات المضلع من قاعدة البيانات"""
    points: List[Union[List[Union[int, float]], dict[str, Union[int, float]]]] = Field(
        ...,
        description="قائمة نقاط المضلع - يمكن أن تكون [[x, y], ...] أو [{'x': x, 'y': y}, ...]"
    )
    image_size: Optional[dict[str, int]] = Field(
        None,
        description="أبعاد الصورة الاختيارية {width, height}"
    )

    @field_validator('points')
    @classmethod
    def validate_points(cls, v):
        """التحقق من صحة نقاط المضلع"""
        if not v or len(v) < 3:
            raise ValueError("يجب أن يحتوي المضلع على 3 نقاط على الأقل")
        
        # التأكد من أن جميع النقاط إما قوائم أو قواميس
        first_type = type(v[0])
        for point in v:
            if not isinstance(point, (list, dict)):
                raise ValueError("يجب أن تكون النقاط قوائم أو قواميس")
            if isinstance(point, list) and first_type == dict:
                raise ValueError("يجب أن تكون جميع النقاط من نفس النوع")
            if isinstance(point, dict) and first_type == list:
                raise ValueError("يجب أن تكون جميع النقاط من نفس النوع")
            
            # التحقق من محتوى النقاط
            if isinstance(point, list):
                if len(point) != 2:
                    raise ValueError("يجب أن تحتوي كل نقطة على قيمتين x و y")
                if not all(isinstance(coord, (int, float)) for coord in point):
                    raise ValueError("يجب أن تكون الإحداثيات أرقاماً")
            elif isinstance(point, dict):
                if 'x' not in point or 'y' not in point:
                    raise ValueError("يجب أن تحتوي كل نقطة على x و y")
                if not all(isinstance(point[k], (int, float)) for k in ['x', 'y']):
                    raise ValueError("يجب أن تكون الإحداثيات أرقاماً")
        
        return v

    @model_validator(mode='after')
    def validate_image_size(self):
        """التحقق من صحة أبعاد الصورة"""
        if self.image_size:
            if 'width' not in self.image_size or 'height' not in self.image_size:
                raise ValueError("يجب أن يحتوي image_size على width و height")
            if not isinstance(self.image_size['width'], (int, float)) or not isinstance(self.image_size['height'], (int, float)):
                raise ValueError("يجب أن يكون width و height أرقاماً")
            if self.image_size['width'] <= 0 or self.image_size['height'] <= 0:
                raise ValueError("يجب أن يكون width و height أرقاماً موجبة")
        return self

    def to_dict(self) -> dict[str, Any]:
        """تحويل النموذج إلى قاموس للإيداع في قاعدة البيانات"""
        return {
            "points": self.points,
            "image_size": self.image_size
        }


class ZoneBase(BaseModel):
    """النموذج الأساسي للمناطق"""
    camera_id: str = Field(..., description="معرف الكاميرا المرتبطة بالمنطقة")
    name: str = Field(..., min_length=1, max_length=100, description="اسم المنطقة")
    zone_type: str = Field(
        ..., 
        min_length=1, 
        max_length=50, 
        description="نوع المنطقة (سيتم تطبيعها تلقائياً: customer_zone→table, staff_zone→work, walkway→walk, kitchen_area→work, entry_exit→walk)"
    )
    polygon_coordinates: PolygonCoordinates = Field(..., description="إحداثيات المضلع")


class ZoneCreate(ZoneBase):
    """نموذج إنشاء منطقة جديدة"""
    parent_zone_id: Optional[str] = Field(None, description="معرف المنطقة الأب (اختياري)")
    excludes_tables: bool = Field(False, description="هل تستثني المنطقة الطاولات؟")
    auto_generated: bool = Field(False, description="هل تم إنشاء المنطقة تلقائياً؟")
    original_zone_type: Optional[str] = Field(None, description="القيمة الأصلية لنوع المنطقة قبل التطبيع")

    @model_validator(mode='after')
    def normalize_zone_type_field(self):
        """تطبيع نوع المنطقة للتوافق مع AI Pipeline مع حفظ القيمة الأصلية"""
        if self.zone_type:
            # حفظ القيمة الأصلية قبل التطبيع إذا لم يتم توفيرها صراحة
            if not self.original_zone_type:
                self.original_zone_type = self.zone_type
            # تطبيع القيمة لقاعدة البيانات
            self.zone_type = normalize_zone_type(self.zone_type)
        return self


class ZoneUpdate(BaseModel):
    """نموذج تعديل منطقة (جميع الحقول اختيارية للتعديل الجزئي)"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="اسم المنطقة")
    zone_type: Optional[str] = Field(None, min_length=1, max_length=50, description="نوع المنطقة")
    polygon_coordinates: Optional[PolygonCoordinates] = Field(None, description="إحداثيات المضلع")
    parent_zone_id: Optional[str] = Field(None, description="معرف المنطقة الأب")
    excludes_tables: Optional[bool] = Field(None, description="هل تستثني المنطقة الطاولات؟")
    auto_generated: Optional[bool] = Field(None, description="هل تم إنشاء المنطقة تلقائياً؟")
    original_zone_type: Optional[str] = Field(None, description="القيمة الأصلية لنوع المنطقة قبل التطبيع")

    @model_validator(mode='after')
    def normalize_zone_type_field(self):
        """تطبيع نوع المنطقة للتوافق مع AI Pipeline مع حفظ القيمة الأصلية"""
        if self.zone_type:
            # حفظ القيمة الأصلية قبل التطبيع إذا لم يتم توفيرها صراحة
            if not self.original_zone_type:
                self.original_zone_type = self.zone_type
            # تطبيع القيمة لقاعدة البيانات
            self.zone_type = normalize_zone_type(self.zone_type)
        return self


class ZoneResponse(BaseModel):
    """نموذج استجابة المنطقة للفرونت إند"""
    id: str
    camera_id: str
    parent_zone_id: Optional[str]
    name: str
    zone_type: str
    original_zone_type: Optional[str]
    polygon_coordinates: dict[str, Any]
    auto_generated: bool
    excludes_tables: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ZoneListResponse(BaseModel):
    """نموذج استجابة قائمة المناطق"""
    zones: List[ZoneResponse]
    total: int