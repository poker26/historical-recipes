"""MinIO client: file upload/download for PDFs and images."""

from io import BytesIO

from minio import Minio
from minio.error import S3Error

from app.config import settings

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region="us-east-1",
        )
        # Ensure bucket exists
        try:
            if not _client.bucket_exists(settings.minio_bucket):
                _client.make_bucket(settings.minio_bucket)
        except S3Error:
            pass  # Bucket may already exist or we lack create permissions
    return _client


def upload_file(data: bytes, path: str, content_type: str = "application/octet-stream") -> str:
    """Upload file to MinIO. Returns the object path."""
    client = get_client()
    client.put_object(
        settings.minio_bucket,
        path,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return path


def download_file(path: str) -> bytes:
    """Download file from MinIO."""
    client = get_client()
    response = client.get_object(settings.minio_bucket, path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_file(path: str) -> None:
    """Delete file from MinIO."""
    client = get_client()
    client.remove_object(settings.minio_bucket, path)


def list_files(prefix: str) -> list[str]:
    """List files in MinIO by prefix."""
    client = get_client()
    objects = client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)
    return [obj.object_name for obj in objects]


def file_exists(path: str) -> bool:
    """Check if file exists in MinIO."""
    client = get_client()
    try:
        client.stat_object(settings.minio_bucket, path)
        return True
    except S3Error:
        return False
