from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
BYTES_PER_MS = (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) // 1000  # 32 bytes/ms
MAX_GAP_PAD_MS = 60_000  # Cap silence zero-padding per jump to 60s to avoid runaway files


class AudioStorageService:
    """Manages local WAV spooling during recording and S3/MinIO persistence + presigned URLs."""

    def __init__(self) -> None:
        self._internal_client = None
        self._public_client = None
        self._s3_available: bool | None = None

    @property
    def spool_dir(self) -> Path:
        p = Path(settings.AUDIO_SPOOL_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fallback_s3_dir(self) -> Path:
        p = Path(settings.LOCAL_S3_FALLBACK_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def spool_path(self, session_id: str) -> Path:
        return self.spool_dir / f"{session_id}.pcm"

    def append_pcm_chunk(
        self, session_id: str, offset_ms: int, pcm_bytes: bytes
    ) -> None:
        """Append 16kHz PCM16 bytes aligned to meeting offset_ms."""
        if not pcm_bytes:
            return
        path = self.spool_path(session_id)
        target_byte = max(0, offset_ms) * BYTES_PER_MS
        # Ensure aligned to 2-byte sample boundary
        target_byte -= target_byte % SAMPLE_WIDTH

        current_size = path.stat().st_size if path.exists() else 0
        with path.open("ab") as fh:
            if target_byte > current_size:
                gap_bytes = min(target_byte - current_size, MAX_GAP_PAD_MS * BYTES_PER_MS)
                fh.write(b"\x00" * gap_bytes)
            fh.write(pcm_bytes)

    def redact_spool_window(
        self, session_id: str, from_ms: int, to_ms: int
    ) -> bool:
        """Zero out PCM bytes in [from_ms, to_ms] on the local spool file."""
        path = self.spool_path(session_id)
        if not path.exists():
            return False
        size = path.stat().st_size
        start_b = max(0, min(from_ms * BYTES_PER_MS, size))
        end_b = max(start_b, min(to_ms * BYTES_PER_MS, size))
        start_b -= start_b % SAMPLE_WIDTH
        end_b -= end_b % SAMPLE_WIDTH
        length = end_b - start_b
        if length <= 0:
            return False
        with path.open("r+b") as fh:
            fh.seek(start_b)
            fh.write(b"\x00" * length)
        return True

    def redact_wav_bytes(self, wav_bytes: bytes, from_ms: int, to_ms: int) -> bytes:
        """Zero out audio frames inside an already-finalized WAV buffer."""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
                params = reader.getparams()
                frames = bytearray(reader.readframes(reader.getnframes()))
            bytes_per_ms = (
                params.framerate * params.nchannels * params.sampwidth
            ) // 1000
            start_b = max(0, min(from_ms * bytes_per_ms, len(frames)))
            end_b = max(start_b, min(to_ms * bytes_per_ms, len(frames)))
            step = params.nchannels * params.sampwidth
            start_b -= start_b % step
            end_b -= end_b % step
            for i in range(start_b, end_b):
                frames[i] = 0
            out = io.BytesIO()
            with wave.open(out, "wb") as writer:
                writer.setparams(params)
                writer.writeframes(bytes(frames))
            return out.getvalue()
        except Exception:
            return wav_bytes

    def build_wav_bytes(self, session_id: str) -> bytes | None:
        path = self.spool_path(session_id)
        if not path.exists() or path.stat().st_size == 0:
            return None
        pcm = path.read_bytes()
        # Trim to even sample width
        if len(pcm) % SAMPLE_WIDTH != 0:
            pcm = pcm[:-1]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        return buf.getvalue()

    def delete_spool_file(self, session_id: str) -> None:
        path = self.spool_path(session_id)
        if path.exists():
            path.unlink(missing_ok=True)

    def _get_internal_client(self):
        if self._internal_client is None:
            self._internal_client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=2,
                    read_timeout=5,
                    retries={"max_attempts": 1},
                ),
            )
        return self._internal_client

    def _get_public_client(self):
        if self._public_client is None:
            self._public_client = boto3.client(
                "s3",
                endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
        return self._public_client

    def _ensure_bucket_sync(self) -> bool:
        client = self._get_internal_client()
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
            self._s3_available = True
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchBucket"):
                try:
                    client.create_bucket(Bucket=settings.S3_BUCKET_NAME)
                    self._s3_available = True
                    return True
                except Exception as create_exc:
                    logger.warning("S3 create_bucket failed: %s", create_exc)
                    self._s3_available = False
                    return False
            logger.warning("S3 head_bucket error: %s", exc)
            self._s3_available = False
            return False
        except (BotoCoreError, Exception) as exc:
            logger.warning("S3 endpoint unreachable (%s); using local fallback", exc)
            self._s3_available = False
            return False

    async def ensure_bucket(self) -> bool:
        return await asyncio.to_thread(self._ensure_bucket_sync)

    def _local_object_path(self, key: str) -> Path:
        safe_key = key.lstrip("/")
        p = self.fallback_s3_dir / safe_key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def upload_wav_bytes(self, session_id: str, wav_bytes: bytes) -> str:
        key = f"sessions/{session_id}/audio.wav"
        if self._s3_available is None:
            await self.ensure_bucket()
        if self._s3_available:
            try:
                client = self._get_internal_client()
                await asyncio.to_thread(
                    client.put_object,
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=key,
                    Body=wav_bytes,
                    ContentType="audio/wav",
                )
                return key
            except Exception as exc:
                logger.warning("S3 upload failed, falling back locally: %s", exc)

        local_path = self._local_object_path(key)
        await asyncio.to_thread(local_path.write_bytes, wav_bytes)
        return key

    async def get_audio_bytes(
        self, key: str, session_id: str | None = None
    ) -> bytes | None:
        if self._s3_available:
            try:
                client = self._get_internal_client()
                resp = await asyncio.to_thread(
                    client.get_object,
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=key,
                )
                return await asyncio.to_thread(resp["Body"].read)
            except Exception as exc:
                logger.warning("S3 get_object failed: %s", exc)

        local_path = self._local_object_path(key)
        if local_path.exists():
            return await asyncio.to_thread(local_path.read_bytes)
        if session_id:
            return self.build_wav_bytes(session_id)
        return None

    async def delete_object(self, key: str) -> None:
        if self._s3_available:
            try:
                client = self._get_internal_client()
                await asyncio.to_thread(
                    client.delete_object,
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=key,
                )
            except Exception as exc:
                logger.warning("S3 delete_object failed: %s", exc)
        local_path = self._local_object_path(key)
        if local_path.exists():
            local_path.unlink(missing_ok=True)

    async def generate_presigned_get_url(
        self, key: str, expires_in: int = 3600
    ) -> str | None:
        if not self._s3_available:
            return None
        try:
            public_client = self._get_public_client()
            return await asyncio.to_thread(
                public_client.generate_presigned_url,
                "get_object",
                Params={"Bucket": settings.S3_BUCKET_NAME, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            logger.warning("Unable to sign presigned URL: %s", exc)
            return None


def make_silence_wav(duration_ms: int = 1000) -> bytes:
    """Utility helper returning valid 16kHz mono WAV bytes."""
    num_bytes = max(SAMPLE_WIDTH, duration_ms * BYTES_PER_MS)
    num_bytes -= num_bytes % SAMPLE_WIDTH
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"\x00" * num_bytes)
    return buf.getvalue()


def format_ms(ms: int) -> str:
    total_sec = max(0, ms // 1000)
    minutes = total_sec // 60
    seconds = total_sec % 60
    return f"{minutes:02d}:{seconds:02d}"


def build_s3_key_or_local_exists(key: str) -> bool:
    return (Path(settings.LOCAL_S3_FALLBACK_DIR) / key.lstrip("/")).exists()


audio_storage = AudioStorageService()
