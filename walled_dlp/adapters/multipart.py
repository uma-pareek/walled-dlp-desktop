"""Pure-stdlib multipart/form-data parser for file upload inspection."""

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as default_policy
from typing import Dict, List, Tuple


@dataclass
class MultipartFile:
    field_name: str
    filename: str
    content_type: str
    data: bytes


def parse_multipart_form_data(
    content_type_header: str, body: bytes
) -> Tuple[Dict[str, str], List[MultipartFile]]:
    """Parse a multipart/form-data HTTP request body into text fields and uploaded files."""
    if not content_type_header or "multipart/form-data" not in content_type_header.lower():
        return {}, []
    if not body:
        return {}, []

    raw_message = (
        f"Content-Type: {content_type_header}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + body
    )
    msg = BytesParser(policy=default_policy).parsebytes(raw_message)

    fields: Dict[str, str] = {}
    files: List[MultipartFile] = []

    if not msg.is_multipart():
        return fields, files

    for part in msg.iter_parts():
        field_name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        part_ctype = part.get_content_type() or "application/octet-stream"

        if filename is not None:
            files.append(
                MultipartFile(
                    field_name=field_name,
                    filename=filename,
                    content_type=part_ctype,
                    data=payload,
                )
            )
        elif field_name:
            fields[field_name] = payload.decode("utf-8", errors="replace")

    return fields, files
