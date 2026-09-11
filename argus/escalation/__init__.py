"""Escalation packets: halt-and-handoff when automation would be unsafe."""

from argus.escalation.models import (
    EscalationOption,
    EscalationPacket,
    RiskLevel,
    SourceType,
)
from argus.escalation.packet import (
    generate_packet_for_product,
    list_packets,
    load_packet,
    save_packet,
)

__all__ = [
    "EscalationOption",
    "EscalationPacket",
    "RiskLevel",
    "SourceType",
    "generate_packet_for_product",
    "list_packets",
    "load_packet",
    "save_packet",
]
