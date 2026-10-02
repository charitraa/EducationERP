from rest_framework import serializers

from .models import StoredFile


class StoredFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoredFile
        fields = ["id", "name", "content_type", "size", "purpose", "uploaded_by", "is_attached", "created_at"]
        read_only_fields = fields


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    purpose = serializers.CharField(max_length=30)
