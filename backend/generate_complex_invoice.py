from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def create_complex_invoice(filename="complex_enterprise_invoice.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, textColor=colors.darkblue)
    normal_style = styles['Normal']
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8)
    
    # Header Info
    story.append(Paragraph("<b>COMMERCIAL INVOICE / PACKING LIST</b>", title_style))
    story.append(Spacer(1, 10))
    
    header_data = [
        [Paragraph("<b>Shipper/Exporter:</b><br/>Global Industrial Spares Ltd.<br/>124 Manufacturing Blvd.<br/>London, UK W1B 3BG<br/>VAT: GB123456789", normal_style),
         Paragraph("<b>Consignee/Importer:</b><br/>TechNova Solutions Pvt Ltd.<br/>Cyber City, Sector 24<br/>Gurugram, Haryana 122002<br/>GSTIN: 06AABCU9603R1Z2", normal_style)],
        
        [Paragraph("<b>Invoice No:</b> INV-2026-9920<br/><b>Date:</b> June 14, 2026<br/><b>PO Number:</b> PO-TN-5521", normal_style),
         Paragraph("<b>Port of Loading:</b> London Gateway<br/><b>Port of Discharge:</b> Nhava Sheva (JNPT)<br/><b>Incoterms:</b> CIF Mumbai", normal_style)]
    ]
    
    t_header = Table(header_data, colWidths=[270, 270])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 15))
    
    # Invoice Line Items Grid (Complex)
    story.append(Paragraph("<b>Line Items Details</b>", styles['Heading3']))
    story.append(Spacer(1, 5))
    
    invoice_data = [
        ["Item", "Product Description & Specifications", "HS Code", "Qty", "UOM", "Unit Price\n(USD)", "Total Value\n(USD)"],
        # Item 1: Telecommunications equipment
        ["1", Paragraph("<b>High-Speed Enterprise Core Router (Model: NX-9900)</b><br/><font size=8>Includes optical transceivers, dual power supplies. Country of Origin: Taiwan</font>", normal_style), "8517.62", "2", "NOS", "4,500.00", "9,000.00"],
        # Item 2: Batteries
        ["2", Paragraph("<b>Lithium-Ion Server Backup Battery System (48V, 200Ah)</b><br/><font size=8>UN3480 compliant. Contains internal BMS module.</font>", normal_style), "8507.60", "15", "NOS", "1,200.00", "18,000.00"],
        # Item 3: Hardware / Fasteners
        ["3", Paragraph("<b>Industrial Stainless Steel Hex Bolts (M16 x 50mm)</b><br/><font size=8>Grade 316, Packed in 50kg crates.</font>", normal_style), "7318.15", "500", "KGS", "4.50", "2,250.00"],
        # Item 4: Electronics
        ["4", Paragraph("<b>Advanced Multimeter Calibration Unit</b><br/><font size=8>With LCD touchscreen and USB interface.</font>", normal_style), "9030.33", "5", "NOS", "850.00", "4,250.00"],
        # Item 5: Parts
        ["5", Paragraph("<b>Molded Plastic Casing for Network Switches</b><br/><font size=8>Polycarbonate material, fire retardant.</font>", normal_style), "3926.90", "120", "NOS", "15.00", "1,800.00"],
    ]
    
    t_items = Table(invoice_data, colWidths=[30, 200, 50, 40, 40, 80, 80])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (2,0), (-1,-1), 'CENTER'),
        ('ALIGN', (5,1), (-1,-1), 'RIGHT'), # Prices right aligned
        ('ALIGN', (6,1), (-1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    
    story.append(t_items)
    story.append(Spacer(1, 20))
    
    # Totals
    totals_data = [
        ["", "", "Subtotal:", "35,300.00"],
        ["", "", "Freight & Insurance:", "1,200.00"],
        ["", "", "Total Invoice Value (USD):", "36,500.00"]
    ]
    t_totals = Table(totals_data, colWidths=[240, 100, 120, 80])
    t_totals.setStyle(TableStyle([
        ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ('FONTNAME', (2,-1), (-1,-1), 'Helvetica-Bold'),
        ('LINEABOVE', (2,-1), (3,-1), 1, colors.black),
    ]))
    story.append(t_totals)
    
    story.append(Spacer(1, 40))
    
    # Declarations
    story.append(Paragraph("<b>Declarations:</b>", styles['Heading4']))
    declarations = """1. We certify that this invoice is true and correct and that the goods are of the origin specified.
2. These commodities, technology, or software were exported from the United Kingdom in accordance with the Export Administration Regulations. Diversion contrary to law is prohibited.
3. Payment Terms: Net 30 Days via Wire Transfer."""
    story.append(Paragraph(declarations.replace('\n', '<br/>'), small_style))
    
    doc.build(story)
    print(f"[+] Complex Native text PDF successfully created: {filename}")

if __name__ == "__main__":
    create_complex_invoice()
