"""Modelos do banco. Importar este pacote registra todas as tabelas em `Base.metadata`."""

from app.models.base import Base
from app.models.catalog import Block, DataSnapshot, Property, Rate
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
    "Tenant",
    "TenantMember",
    "TenantRoute",
    "WeeklyReport",
    "WhatsAppAccount",
]
