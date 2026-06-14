from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def create_native_invoice(filename="sample_invoice.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    # Header Info
    story.append(Paragraph("<b>COMMERCIAL INVOICE / PACKING LIST</b>", styles['Title']))
    story.append(Spacer(1, 12))
    
    meta_data = [
        [Paragraph("<b>Exporter:</b><br/>Global Industrial Spares Ltd.<br/>London, UK", styles['Normal']),
         Paragraph("<b>Invoice No:</b> INV-2026-8891<br/><b>Date:</b> June 13, 2026<br/><b>Port of Loading:</b> London Gateway", styles['Normal'])]
    ]
    t_meta = Table(meta_data, colWidths=[270, 270])
    story.append(t_meta)
    story.append(Spacer(1, 20))
    
    # Invoice Line Items Grid (The Core Target for your LLM)
    # This matrix contains clear, searchable text blocks that pymupdf4llm can capture
    invoice_data = [
        ["Item No", "Product Description", "Qty", "Unit Price (USD)", "Total Value"],
        ["1", "Lithium-Ion Battery Pack (12V, 100Ah) for industrial energy storage systems", "50", "320.00", "16,000.00"],
        ["2", "High-Speed Network Routing Switch (Model: NX-8400) with optical fiber ports", "12", "1,150.00", "13,800.00"],
        ["3", "Stainless Steel Hexagonal Nuts (M12 Industrial Grade, Pack of 500)", "5", "45.00", "225.00"],
        ["4", "Digital Multimeter Calibration Module with LCD display interface", "3", "410.00", "1,230.00"]
    ]
    
    t_items = Table(invoice_data, colWidths=[50, 270, 40, 90, 90])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    
    story.append(t_items)
    doc.build(story)
    print(f"[+] Native text PDF successfully created: {filename}")

if __name__ == "__main__":
    create_native_invoice()
