"""
Lee un certificado de curso (foto o PDF) y extrae sus datos usando Claude.

Uso:
  from scripts.extraer_certificado import extraer_datos_certificado
  datos = extraer_datos_certificado(bytes_del_archivo)
"""
import base64
import sys
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

SYSTEM_PROMPT = (
    "Eres un extractor de datos de certificados de cursos de capacitacion de una "
    "empresa del sector salud. Lee el documento y extrae los datos solicitados "
    "exactamente como aparecen. Las fechas van en formato YYYY-MM-DD. "
    "Si un dato no aparece en el documento, dejalo vacio o nulo — no inventes valores."
)

USER_PROMPT = (
    "Extrae del certificado: cedula del empleado, nombre, apellido, nombre del curso, "
    "fecha de realizacion y fecha de vencimiento (si el certificado no indica "
    "vencimiento, dejalo vacio). Indica ademas tu nivel de confianza en la lectura, "
    "de 0 a 1."
)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


class CertificadoExtraido(BaseModel):
    cedula: Optional[str] = None
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    curso: Optional[str] = None
    fecha_realizacion: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    confianza: float = 0.0


def _detectar_tipo(datos: bytes) -> tuple[str, str]:
    """Devuelve (tipo_bloque, media_type) segun la firma binaria del archivo."""
    if datos[:4] == b"%PDF":
        return "document", "application/pdf"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png"
    if datos[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg"
    return "image", "image/jpeg"


def extraer_datos_certificado(datos: bytes) -> CertificadoExtraido:
    """Envia el certificado a Claude y devuelve los datos extraidos y validados."""
    tipo_bloque, media_type = _detectar_tipo(datos)
    b64 = base64.standard_b64encode(datos).decode("utf-8")

    response = _client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": tipo_bloque,
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {"type": "text", "text": USER_PROMPT},
            ],
        }],
        output_format=CertificadoExtraido,
    )
    return response.parsed_output
