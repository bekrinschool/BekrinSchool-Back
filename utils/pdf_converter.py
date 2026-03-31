"""
Convert PDF to PNG images for exam display.
Uses PyMuPDF (fitz) first (fast, no external dependencies). Falls back to pdf2image if needed.
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)


class PdfConversionError(RuntimeError):
    """Raised when PDF could not be converted to images (e.g. Poppler missing and PyMuPDF failed)."""
    pass

# Common Windows Poppler paths if not in PATH
POPPLER_PATHS_WINDOWS = [
    r"C:\Program Files\poppler\Library\bin",
    r"C:\poppler\Library\bin",
    r"C:\Program Files (x86)\poppler\Library\bin",
]


def _get_poppler_path():
    """Return poppler_path for Windows if we can find it; None otherwise. Checks POPPLER_PATH env first."""
    env_path = os.environ.get("POPPLER_PATH") or os.environ.get("POPPLER_HOME")
    if env_path:
        path = env_path.rstrip(os.sep)
        bin_path = os.path.join(path, "Library", "bin") if not path.endswith("bin") else path
        if os.path.isdir(bin_path):
            return bin_path
        if os.path.isdir(path):
            return path
    if sys.platform != "win32":
        return None
    for path in POPPLER_PATHS_WINDOWS:
        if os.path.isdir(path):
            return path
    return None


def _convert_with_fitz(pdf_path, output_dir, dpi=200):
    """Convert PDF to images using PyMuPDF (no Poppler required). Returns dict {success, pages, error}."""
    try:
        import fitz  # PyMuPDF
        os.makedirs(output_dir, exist_ok=True)
        image_paths = []
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        doc = fitz.open(pdf_path)
        try:
            for i in range(len(doc)):
                page = doc[i]
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                filename = f"page_{i + 1:03d}.jpg"
                filepath = os.path.join(output_dir, filename)
                pix.save(filepath)
                image_paths.append(filepath)
        finally:
            doc.close()
        logger.info("pdf_convert fitz_ok pdf_path=%s output_dir=%s pages=%s", pdf_path, output_dir, len(image_paths))
        return {"success": True, "pages": image_paths, "error": ""}
    except ModuleNotFoundError as e:
        logger.error("pdf_convert fitz_missing pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        return {"success": False, "pages": [], "error": f"PDF conversion failed: PyMuPDF not installed ({e})"}
    except FileNotFoundError as e:
        logger.error("pdf_convert fitz_file_missing pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        return {"success": False, "pages": [], "error": f"PDF conversion failed: file not found ({e})"}
    except Exception as e:
        logger.error("pdf_convert fitz_failed pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        return {"success": False, "pages": [], "error": f"PDF conversion failed: {e}"}


def convert_pdf_to_images(pdf_path, output_dir, dpi=200, poppler_path=None):
    """
    Convert a PDF file to images, one per page.
    Priority order:
    1) PyMuPDF (fitz) — recommended, no external dependency
    2) pdf2image (Poppler) — fallback

    :param pdf_path: Absolute path to the PDF file.
    :param output_dir: Directory to write page_1.png, page_2.png, ...
    :param dpi: Resolution for rendering (default 200).
    :param poppler_path: Optional path to Poppler bin (for Windows when not in PATH).
    :return: dict: {"success": bool, "pages": [abs_path...], "error": str}
    """
    if not os.path.isfile(pdf_path):
        logger.error("pdf_convert pdf_missing pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        return {"success": False, "pages": [], "error": f"PDF conversion failed: PDF file not found: {pdf_path}"}

    os.makedirs(output_dir, exist_ok=True)

    # 1) Try PyMuPDF (fitz) first
    fitz_res = _convert_with_fitz(pdf_path, output_dir, dpi=dpi)
    if fitz_res.get("success"):
        return fitz_res

    # 2) Fallback: pdf2image (Poppler)
    try:
        from pdf2image import convert_from_path
    except (ImportError, ModuleNotFoundError) as e:
        logger.error("pdf_convert pdf2image_missing pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        # Preserve fitz error if we have one, otherwise show missing module message.
        msg = fitz_res.get("error") or f"PDF conversion failed: pdf2image not installed ({e})"
        return {"success": False, "pages": [], "error": msg}

    kwargs = {"dpi": dpi}
    if poppler_path and os.path.isdir(poppler_path):
        kwargs["poppler_path"] = poppler_path

    try:
        pages = convert_from_path(pdf_path, **kwargs)
        image_paths = []
        for i, page in enumerate(pages):
            filename = f"page_{i + 1:03d}.jpg"
            filepath = os.path.join(output_dir, filename)
            page.save(filepath, "JPEG")
            image_paths.append(filepath)
        logger.info("pdf_convert pdf2image_ok pdf_path=%s output_dir=%s pages=%s", pdf_path, output_dir, len(image_paths))
        return {"success": True, "pages": image_paths, "error": ""}
    except ModuleNotFoundError as e:
        logger.error("pdf_convert pdf2image_missing_runtime pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        msg = fitz_res.get("error") or f"PDF conversion failed: pdf2image not available ({e})"
        return {"success": False, "pages": [], "error": msg}
    except FileNotFoundError as e:
        logger.error("pdf_convert pdf2image_file_missing pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        return {"success": False, "pages": [], "error": f"PDF conversion failed: file not found ({e})"}
    except Exception as e:
        logger.error("pdf_convert pdf2image_failed pdf_path=%s output_dir=%s", pdf_path, output_dir, exc_info=True)
        hint = ""
        if sys.platform == "win32":
            hint = " (pdf2image requires Poppler on Windows; ensure Poppler is installed and pdftoppm is in PATH or set POPPLER_PATH)"
        # Prefer fitz error if it exists; otherwise use pdf2image error.
        msg = fitz_res.get("error") or f"PDF conversion failed: {e}{hint}"
        return {"success": False, "pages": [], "error": msg}
