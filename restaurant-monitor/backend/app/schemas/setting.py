# DTOs إعدادات النظام
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SystemSettingBase(BaseModel):
    """النموذج الأساسي لإعدادات النظام"""
    worker_idle_limit: int = Field(
        ...,
        ge=10,       # الحد الأدنى: 10 ثواني
        le=3600,     # الحد الأقصى: 60 دقيقة (3600 ثانية)
        description="الحد الزمني للخمول (بالثواني) - يجب أن يكون بين 10 و 3600 ثانية"
    )

    @field_validator('worker_idle_limit')
    @classmethod
    def validate_idle_limit(cls, v):
        """التحقق من صحة حد الخمول"""
        if v < 10:
            raise ValueError("الحد الأدنى للخمول هو 10 ثواني")
        if v > 3600:
            raise ValueError("الحد الأقصى للخمول هو 60 دقيقة (3600 ثانية)")
        
        # التحقق من القيم الشائعة المفضلة
        common_values = [30, 60, 90, 120, 180, 300, 600]
        if v not in common_values:
            # يمكن إضافة تحذير لكنه ليس خطأ
            pass
            
        return v


class SystemSettingCreate(SystemSettingBase):
    """نموذج إنشاء إعدادات النظام"""
    pass


class SystemSettingUpdate(BaseModel):
    """نموذج تعديل إعدادات النظام (تعديل جزئي)"""
    worker_idle_limit: Optional[int] = Field(
        None,
        ge=10,
        le=3600,
        description="الحد الزمني للخمول (بالثواني) - يجب أن يكون بين 10 و 3600 ثانية"
    )

    @field_validator('worker_idle_limit')
    @classmethod
    def validate_idle_limit(cls, v):
        """التحقق من صحة حد الخمول عند التعديل"""
        if v is not None:
            if v < 10:
                raise ValueError("الحد الأدنى للخمول هو 10 ثواني")
            if v > 3600:
                raise ValueError("الحد الأقصى للخمول هو 60 دقيقة (3600 ثانية)")
        return v


class SystemSettingResponse(BaseModel):
    """نموذج استجابة إعدادات النظام"""
    id: int
    worker_idle_limit: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SystemSettingDetailResponse(SystemSettingResponse):
    """نموذج استجابة مفصل لإعدادات النظام مع معلومات إضافية"""
    worker_idle_limit_minutes: float = Field(
        ...,
        description="الحد الزمني للخمول بالدقيقة"
    )
    worker_idle_limit_formatted: str = Field(
        ...,
        description="الحد الزمني للخمول بصيغة مقروءة"
    )

    @classmethod
    def from_model(cls, setting: "SystemSetting"):
        """إنشاء استجابة مفصلة من الموديل"""
        return cls(
            id=setting.id,
            worker_idle_limit=setting.worker_idle_limit,
            updated_at=setting.updated_at,
            worker_idle_limit_minutes=round(setting.worker_idle_limit / 60, 2),
            worker_idle_limit_formatted=cls._format_time(setting.worker_idle_limit)
        )

    @staticmethod
    def _format_time(seconds: int) -> str:
        """تحويل الثواني إلى صيغة مقروءة"""
        if seconds < 60:
            return f"{seconds} ثانية"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} دقيقة"
        else:
            hours = seconds // 3600
            remaining_minutes = (seconds % 3600) // 60
            if remaining_minutes > 0:
                return f"{hours} ساعة و {remaining_minutes} دقيقة"
            else:
                return f"{hours} ساعة"