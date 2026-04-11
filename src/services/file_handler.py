"""Download and extract text from files sent via QQ."""

import logging
import os
import re
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

EXTRACTABLE_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".go", ".java", ".c", ".cpp", ".h",
    ".html", ".css", ".sql", ".sh", ".bat", ".log", ".ini", ".conf",
    ".docx", ".xlsx", ".pdf",
}

MAX_EXTRACT_SIZE = 15000


async def download_file(http_client: httpx.AsyncClient, file_id: str, save_dir: str) -> dict | None:
    """Call NapCat get_file API and download the file. Returns file info dict or None."""
    try:
        resp = await http_client.post("/get_file", json={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.warning("get_file failed: %s", data)
            return None

        file_info = data.get("data", {})
        file_name = file_info.get("file_name", "unknown")
        b64_data = file_info.get("base64", "")
        file_url = file_info.get("url", "")

        os.makedirs(save_dir, exist_ok=True)
        target_path = os.path.join(save_dir, file_name)

        import base64
        if b64_data:
            with open(target_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
        elif file_url and file_url.startswith("http"):
            dl_resp = await http_client.get(file_url, timeout=60)
            dl_resp.raise_for_status()
            with open(target_path, "wb") as f:
                f.write(dl_resp.content)
        else:
            logger.warning("No base64 or download URL in get_file response")
            return None

        logger.info("File saved: %s (%s bytes)", target_path, os.path.getsize(target_path))
        file_info["local_path"] = target_path
        return file_info

    except Exception:
        logger.exception("Failed to download file %s", file_id)
        return None


def extract_text(file_path: str) -> str | None:
    """Extract readable text from a file. Returns None if not extractable."""
    ext = Path(file_path).suffix.lower()

    if ext not in EXTRACTABLE_EXTENSIONS:
        return None

    try:
        if ext == ".docx":
            return _extract_docx(file_path)
        elif ext == ".xlsx":
            return _extract_xlsx(file_path)
        elif ext == ".pdf":
            return _extract_pdf(file_path)
        else:
            with open(file_path, "r", errors="replace") as f:
                text = f.read(MAX_EXTRACT_SIZE)
            if len(text) >= MAX_EXTRACT_SIZE:
                text += "\n\n...(文件内容过长，已截断)"
            return text
    except Exception:
        logger.exception("Failed to extract text from %s", file_path)
        return None


def _extract_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)
    if len(text) > MAX_EXTRACT_SIZE:
        text = text[:MAX_EXTRACT_SIZE] + "\n\n...(文件内容过长，已截断)"
    return text


def _extract_xlsx(path: str) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    lines = []
    for sheet in wb.sheetnames[:3]:
        ws = wb[sheet]
        lines.append(f"=== Sheet: {sheet} ===")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 50:
                lines.append("...(行数过多，已截断)")
                break
            lines.append("\t".join(str(c) if c is not None else "" for c in row))
    text = "\n".join(lines)
    if len(text) > MAX_EXTRACT_SIZE:
        text = text[:MAX_EXTRACT_SIZE] + "\n\n...(文件内容过长，已截断)"
    return text


def _extract_pdf(path: str) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages[:20]):
        text = page.extract_text()
        if text:
            pages.append(f"--- Page {i+1} ---\n{text}")
    text = "\n\n".join(pages)
    if len(text) > MAX_EXTRACT_SIZE:
        text = text[:MAX_EXTRACT_SIZE] + "\n\n...(文件内容过长，已截断)"
    return text


def parse_cq_files(raw_message: str) -> list[dict]:
    """Extract CQ:file and CQ:image segments from raw message."""
    results = []
    for match in re.finditer(r"\[CQ:(file|image),([^\]]+)\]", raw_message):
        cq_type = match.group(1)
        params = {}
        for kv in match.group(2).split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v
        results.append({"type": cq_type, "params": params, "raw": match.group(0)})
    return results
