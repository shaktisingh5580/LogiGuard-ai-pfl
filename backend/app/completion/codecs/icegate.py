"""ICEGATE Codec — generates India customs filing XML."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from app.completion.codecs.base import GatewayCodec
from app.completion.mapper import StatutoryFields

logger = logging.getLogger(__name__)


class ICEGATECodec(GatewayCodec):
    """Generates ICEGATE-compatible Bill of Entry XML for Indian customs."""

    @property
    def gateway_name(self) -> str:
        return "ICEGATE (India)"

    def encode(
        self,
        hs_code: str,
        description: str,
        statutory_fields: StatutoryFields,
        **kwargs,
    ) -> str:
        """Generate ICEGATE Bill of Entry XML payload."""
        quantity = kwargs.get("quantity", 1)
        unit_price = kwargs.get("unit_price", 0.0)
        country_of_origin = kwargs.get("country_of_origin", "UNKNOWN")

        root = Element("BillOfEntry")
        root.set("xmlns", "http://icegate.gov.in/boe/v3")
        root.set("version", "3.0")

        # Header
        header = SubElement(root, "Header")
        SubElement(header, "GeneratedAt").text = datetime.now(timezone.utc).isoformat()
        SubElement(header, "Gateway").text = "ICEGATE"
        SubElement(header, "Jurisdiction").text = "IN"

        # Item
        item = SubElement(root, "Item")
        SubElement(item, "HSCode").text = hs_code
        SubElement(item, "Description").text = description
        SubElement(item, "Quantity").text = str(quantity)
        SubElement(item, "UnitPrice").text = f"{unit_price:.2f}"
        SubElement(item, "CountryOfOrigin").text = country_of_origin

        # Duty rates
        duties = SubElement(item, "DutyRates")
        for rate in statutory_fields.duty_rates:
            duty = SubElement(duties, "Duty")
            SubElement(duty, "Type").text = rate.duty_type
            SubElement(duty, "Rate").text = f"{rate.rate:.4f}"
            SubElement(duty, "RateType").text = rate.rate_type
            if rate.notification:
                SubElement(duty, "NotificationNo").text = rate.notification

        SubElement(item, "TotalDutyPercent").text = f"{statutory_fields.total_duty_percent:.2f}"

        # Exemptions
        if statutory_fields.exemptions:
            exemptions = SubElement(item, "Exemptions")
            for ex in statutory_fields.exemptions:
                exempt = SubElement(exemptions, "Exemption")
                SubElement(exempt, "NotificationNo").text = ex.notification_number
                SubElement(exempt, "Description").text = ex.description

        # PGA flags
        if statutory_fields.pga_flags:
            pga = SubElement(item, "PGARequirements")
            for flag in statutory_fields.pga_flags:
                SubElement(pga, "Agency").text = flag

        xml_bytes = tostring(root, encoding="unicode", xml_declaration=True)
        return xml_bytes

    def validate(self, payload: str) -> tuple[bool, list[str]]:
        """Validate against ICEGATE XSD schema.

        In production, this validates against the official ICEGATE XSD.
        For the demo, we do basic structural validation.
        """
        errors = []
        try:
            from xml.etree.ElementTree import fromstring
            root = fromstring(payload)

            # Check required elements
            if root.find("Header") is None:
                errors.append("Missing <Header> element")
            if root.find("Item") is None:
                errors.append("Missing <Item> element")

            item = root.find("Item")
            if item is not None:
                if item.find("HSCode") is None:
                    errors.append("Missing <HSCode> in <Item>")
                if item.find("Description") is None:
                    errors.append("Missing <Description> in <Item>")
                if item.find("DutyRates") is None:
                    errors.append("Missing <DutyRates> in <Item>")

        except Exception as e:
            errors.append(f"XML parsing error: {e}")

        return (len(errors) == 0, errors)
