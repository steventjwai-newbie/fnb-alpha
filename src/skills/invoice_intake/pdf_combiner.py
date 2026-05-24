from pathlib import Path


def combine_to_pdf(file_paths: list[str], output_path: str) -> str:
    """Combine images and PDFs into a single PDF at output_path. Returns output_path."""
    from PIL import Image, ImageOps
    from pypdf import PdfWriter, PdfReader
    import io

    writer = PdfWriter()

    for fp in file_paths:
        p = Path(fp)
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            reader = PdfReader(fp)
            for page in reader.pages:
                writer.add_page(page)
        else:
            img = ImageOps.exif_transpose(Image.open(fp)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PDF")
            buf.seek(0)
            reader = PdfReader(buf)
            for page in reader.pages:
                writer.add_page(page)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
