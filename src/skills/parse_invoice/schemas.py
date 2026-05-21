class LineItem(BaseModel):
    raw_description: str        # exactly as printed
    quantity: Decimal
    unit: str | None            # kg, pcs, btl
    unit_price: Decimal
    line_total: Decimal
    confidence: float

class Invoice(BaseModel):
    supplier_name: str
    invoice_number: str
    invoice_date: date
    currency: str               # extracted, default MYR
    subtotal: Decimal
    tax: Decimal | None
    total: Decimal
    line_items: list[LineItem]
    extraction_method: Literal["azure_di", "gemini_fallback"]
    overall_confidence: float
    source_pdf_path: str