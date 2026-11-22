"""Modelos do banco. Importar este pacote registra todas as tabelas em `Base.metadata`."""

from app.models.base import Base
from app.models.catalog import Block, CalendarFeed, DataSnapshot, Property, Rate, SheetLink
from app.models.compliance import AuditLog, Consent, PrivacyNotice, WeeklyReport
from app.models.conversation import Contact, Conversation, Handoff, Intent, Message, SearchResult
from app.models.tenant import AppUser, Tenant, TenantMember, TenantRoute, WhatsAppAccount

GLOBAL_TABLES = frozenset({"app_user", "tenant_route", "alembic_version"})
"""Tabelas sem `tenant_id`, de propósito (ver DECISIONS, D011). `tenant` usa `id`."""

__all__ = [
    "GLOBAL_TABLES",
    "AppUser",
    "AuditLog",
    "Base",
    "Block",
    "CalendarFeed",
    "Consent",
    "Contact",
    "Conversation",
    "DataSnapshot",
    "Handoff",
    "Intent",
    "Message",
    "PrivacyNotice",
    "Property",
    "Rate",
    "SearchResult",
    "SheetLink",
    "Tenant",
    "TenantMember",
    "TenantRoute",
    "WeeklyReport",
    "WhatsAppAccount",
]
