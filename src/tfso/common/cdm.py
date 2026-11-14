"""Canonical Data Model (CDM) for the Sales Order data product - contract version 1.

This module is the single source of truth for the contract. `python -m tfso.tools.export_schema`
renders it to cdm/sales-order.v1.schema.json (JSON Schema 2020-12). Elements are aligned to
ANSI X12 (850/855/856/810/997), GS1 identifiers (GTIN, GLN, SSCC), OAGIS SalesOrder and
Dynamics 365 Finance & Operations data entities (SalesOrderHeadersV2 / SalesOrderLines).

JSON uses camelCase; Python uses snake_case (alias generator).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .statuses import HistoryEventType, LineStatus, OrderStatus, StatusCategory

CDM_VERSION = "1.1.0"  # 1.1.0: additive `fulfillment` (region, site, account scope, sourcing decision)


class CdmModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore",
                              use_enum_values=True, ser_json_timedelta="iso8601")

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# --------------------------------------------------------------------------- enumerations
class SourceChannel(str, Enum):
    EDI = "EDI"
    EMAIL = "EMAIL"
    PORTAL = "PORTAL"
    MANUAL = "MANUAL"
    PARTNER_API = "PARTNER_API"


class SourceDocumentType(str, Enum):
    X12_850 = "X12_850"
    X12_860 = "X12_860"
    EMAIL_MESSAGE = "EMAIL_MESSAGE"
    PDF = "PDF"
    SPREADSHEET = "SPREADSHEET"
    PORTAL_FORM = "PORTAL_FORM"
    PARTNER_JSON = "PARTNER_JSON"


class OrderType(str, Enum):
    STANDARD = "STANDARD"
    RUSH = "RUSH"
    STANDING = "STANDING"
    BLANKET_RELEASE = "BLANKET_RELEASE"
    DROP_SHIP = "DROP_SHIP"
    SAMPLE = "SAMPLE"
    REPLACEMENT = "REPLACEMENT"


class OrderPurpose(str, Enum):
    ORIGINAL = "ORIGINAL"      # X12 BEG01 = 00
    CHANGE = "CHANGE"          # BEG01 = 04 / 860
    CANCELLATION = "CANCELLATION"  # BEG01 = 01
    REPLACE = "REPLACE"        # BEG01 = 05
    CONFIRMATION = "CONFIRMATION"  # BEG01 = 06 (confirming a phone order)


class TemperatureZone(str, Enum):
    FROZEN = "FROZEN"
    REFRIGERATED = "REFRIGERATED"
    AMBIENT = "AMBIENT"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ExceptionState(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    OVERRIDDEN = "OVERRIDDEN"


class AckType(str, Enum):
    FUNCTIONAL_ACK_997 = "FUNCTIONAL_ACK_997"
    PO_ACK_855 = "PO_ACK_855"
    SHIP_NOTICE_856 = "SHIP_NOTICE_856"
    INVOICE_810 = "INVOICE_810"
    EMAIL_CONFIRMATION = "EMAIL_CONFIRMATION"
    PORTAL_NOTIFICATION = "PORTAL_NOTIFICATION"
    WEBHOOK = "WEBHOOK"


class Tier(str, Enum):
    TIER1 = "TIER1"
    TIER2 = "TIER2"
    TIER3 = "TIER3"


class ActorType(str, Enum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    CUSTOMER = "CUSTOMER"
    PARTNER = "PARTNER"


class RelationshipType(str, Enum):
    SAME_INTERCHANGE = "SAME_INTERCHANGE"
    SPLIT_FROM = "SPLIT_FROM"
    SPLIT_INTO = "SPLIT_INTO"
    BACKORDER_OF = "BACKORDER_OF"
    REPLACES = "REPLACES"
    REPLACED_BY = "REPLACED_BY"
    CHANGE_OF = "CHANGE_OF"
    DUPLICATE_OF = "DUPLICATE_OF"


# --------------------------------------------------------------------------- shared value objects
class Actor(CdmModel):
    type: ActorType = ActorType.SYSTEM
    id: str = Field(description="User principal, service name or trading-partner id")
    name: str | None = None


class Address(CdmModel):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country: str = Field("US", description="ISO 3166-1 alpha-2")


class Contact(CdmModel):
    role: str | None = Field(None, description="X12 PER01 e.g. BD (buyer), OC (order contact)")
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Money(CdmModel):
    amount: Decimal
    currency: str = Field("USD", description="ISO 4217")


class Quantity(CdmModel):
    value: Decimal = Field(description="Quantity in the stated unit of measure")
    uom: str = Field(description="X12 355 unit code: CA (case), EA, LB, BX, PL (pallet)")


class Reference(CdmModel):
    qualifier: str = Field(description="X12 REF01 / OAGIS DocumentReference type, e.g. DP (department), CT (contract), IA (vendor #)")
    value: str
    description: str | None = None


class Note(CdmModel):
    type: Literal["CUSTOMER", "INTERNAL", "SHIPPING", "RECEIVING"] = "CUSTOMER"
    text: str
    created_by: str | None = None
    created_at: datetime | None = None


class DateWindow(CdmModel):
    start: datetime | None = None
    end: datetime | None = None


# --------------------------------------------------------------------------- source / lineage
class ExtractionInfo(CdmModel):
    method: Literal["STRUCTURED", "TEMPLATE", "OCR", "LLM", "MANUAL"] = "STRUCTURED"
    confidence: float = Field(1.0, ge=0, le=1)
    model_id: str | None = None
    fields_low_confidence: list[str] = Field(default_factory=list)


class SourceDocument(CdmModel):
    document_type: SourceDocumentType
    document_id: str | None = Field(None, description="Pointer to the stored raw document (documents API / bronze)")
    reference: str | None = Field(None, description="Channel reference: email Message-ID, file name, portal session")
    sender_id: str | None = Field(None, description="ISA06 / email sender / partner client id")
    receiver_id: str | None = Field(None, description="ISA08")
    interchange_control_number: str | None = Field(None, description="ISA13")
    group_control_number: str | None = Field(None, description="GS06")
    transaction_set_control_number: str | None = Field(None, description="ST02")
    x12_version: str | None = Field(None, description="GS08, e.g. 004010")
    received_at: datetime | None = None


class SourceInfo(CdmModel):
    channel: SourceChannel
    channel_detail: str | None = Field(None, description="e.g. VAN name, mailbox, partner platform")
    received_at: datetime
    source_document: SourceDocument
    extraction: ExtractionInfo | None = None
    submitted_by: Actor | None = None


class LineageStep(CdmModel):
    step: Literal["CAPTURE", "INGEST", "VALIDATE", "ENRICH", "CANONICALIZE", "PUBLISH", "ORCHESTRATE", "SYNC"]
    service: str
    at: datetime
    input_ref: str | None = None
    output_ref: str | None = None


class AuditInfo(CdmModel):
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    contract_version: str = CDM_VERSION
    lineage: list[LineageStep] = Field(default_factory=list)


# --------------------------------------------------------------------------- header elements
class PurchaseOrderReference(CdmModel):
    po_number: str = Field(description="Customer purchase order number (X12 BEG03)")
    po_date: date | None = Field(None, description="BEG05")
    release_number: str | None = Field(None, description="BEG04 - blanket release")
    contract_number: str | None = Field(None, description="REF*CT")
    department: str | None = Field(None, description="REF*DP")
    promotion_deal_number: str | None = Field(None, description="REF*PD")


class ErpReference(CdmModel):
    system: str = "D365FO"
    company: str = Field(description="D365 legal entity (dataAreaId)")
    sales_order_id: str | None = Field(None, description="D365 SalesOrderNumber")
    sales_order_status: str | None = Field(None, description="D365 SalesStatus: Backorder, Delivered, Invoiced, Canceled")
    customer_account: str | None = Field(None, description="OrderingCustomerAccountNumber")
    invoice_account: str | None = Field(None, description="InvoiceCustomerAccountNumber")
    site_id: str | None = None
    warehouse_id: str | None = None
    delivery_mode: str | None = None
    created_at: datetime | None = None
    last_synced_at: datetime | None = None
    rejection_reason: str | None = None


class Party(CdmModel):
    role: Literal["SOLD_TO", "SHIP_TO", "BILL_TO", "PAYER", "BUYER", "SUPPLIER", "CARRIER", "SHIP_FROM"]
    party_id: str | None = Field(None, description="Data product party key (customer id or ship-to id)")
    customer_id: str | None = Field(None, description="Owning customer id (for SHIP_TO / BILL_TO)")
    erp_account: str | None = Field(None, description="D365 customer account / delivery address location id")
    name: str | None = None
    gln: str | None = Field(None, description="GS1 Global Location Number (N104 qualifier UL)")
    duns: str | None = Field(None, description="DUNS (N104 qualifier 1)")
    edi_location_code: str | None = Field(None, description="Buyer-assigned location code (N104 qualifier 92)")
    address: Address | None = None
    contacts: list[Contact] = Field(default_factory=list)
    receiving_hours: str | None = None
    delivery_days: list[str] = Field(default_factory=list, description="Days the location accepts deliveries")


class Parties(CdmModel):
    sold_to: Party
    ship_to: Party
    bill_to: Party | None = None
    payer: Party | None = None
    buyer: Party | None = None
    supplier: Party | None = None
    ship_from: Party | None = None
    carrier: Party | None = None


class OrderDates(CdmModel):
    order_date: date | None = Field(None, description="Customer PO date")
    requested_delivery_date: date | None = Field(None, description="DTM*002")
    requested_ship_date: date | None = Field(None, description="DTM*010; derived from transit time when absent")
    promised_ship_date: date | None = Field(None, description="Confirmed by D365 (855 DTM*068)")
    promised_delivery_date: date | None = None
    cancel_by_date: date | None = Field(None, description="DTM*001 cancel-after")
    delivery_window: DateWindow | None = None


class PaymentTerms(CdmModel):
    code: str = Field(description="D365 terms of payment code, e.g. Net30")
    description: str | None = None
    net_days: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None


class Commercial(CdmModel):
    currency: str = "USD"
    payment_terms: PaymentTerms | None = None
    price_agreement_id: str | None = Field(None, description="Trade agreement journal applied")
    customer_price_group: str | None = None
    incoterms: str | None = Field(None, description="Incoterms 2020 code, e.g. FCA, DAP")
    fob_point: str | None = Field(None, description="X12 FOB01: PP prepaid, CC collect")
    sales_responsible: str | None = None
    tax_exempt_number: str | None = None


class TemperatureRequirement(CdmModel):
    zone: TemperatureZone = TemperatureZone.REFRIGERATED
    min_celsius: Decimal | None = None
    max_celsius: Decimal | None = None


class ShippingInstructions(CdmModel):
    delivery_mode: str | None = Field(None, description="D365 mode of delivery, e.g. TRUCK-REEFER")
    transport_responsibility: Literal["SUPPLIER_DELIVERED", "CUSTOMER_PICKUP", "THIRD_PARTY"] = "SUPPLIER_DELIVERED"
    carrier_scac: str | None = Field(None, description="Standard Carrier Alpha Code (TD503)")
    carrier_name: str | None = None
    service_level: str | None = None
    routing: str | None = None
    ship_from_site: str | None = None
    ship_from_warehouse: str | None = None
    temperature: TemperatureRequirement | None = None
    appointment_required: bool = False
    pallet_type: str | None = None
    special_instructions: str | None = None


class SourcingCandidate(CdmModel):
    warehouse: str
    site_id: str | None = None
    region_id: str | None = None
    fill_rate: float = Field(description="Share of ordered quantity the site can fill from on-hand (0-1)")
    home: bool = Field(False, description="Primary site of the ship-to's region")
    chosen: bool = False


class SourcingDecision(CdmModel):
    rule: Literal["HOME_SITE", "ALTERNATE_SITE_IN_REGION", "CROSS_REGION", "BEST_AVAILABLE"]
    reason: str
    decided_at: datetime
    candidates: list[SourcingCandidate] = Field(default_factory=list)


class Fulfillment(CdmModel):
    """Where the order is fulfilled in the regional network (added in contract 1.1.0)."""
    region_id: str = Field(description="Fulfilling operating region, e.g. WEST, CENTRAL, NORTHEAST")
    region_name: str | None = None
    customer_region_id: str | None = Field(None, description="Region the ship-to belongs to (demand region)")
    site_id: str | None = None
    warehouse: str | None = Field(None, description="D365 warehouse that ships the order")
    site_name: str | None = None
    account_scope: Literal["NATIONAL", "REGIONAL"] | None = Field(
        None, description="NATIONAL accounts are served from more than one region")
    cross_region: bool = Field(False, description="Fulfilled outside the ship-to's home region")
    sourcing: SourcingDecision | None = None


# --------------------------------------------------------------------------- lines
class ItemReference(CdmModel):
    item_number: str | None = Field(None, description="Taylor Farms item number (D365 ItemNumber)")
    buyer_item_number: str | None = Field(None, description="PO1 qualifier IN")
    vendor_item_number: str | None = Field(None, description="PO1 qualifier VN")
    gtin: str | None = Field(None, description="GS1 GTIN-14 (case)")
    upc: str | None = Field(None, description="GTIN-12 consumer unit (PO1 qualifier UP)")
    description: str | None = None
    brand: str | None = None
    pack_size: str | None = Field(None, description="e.g. 6x2 LB")
    temperature_zone: TemperatureZone | None = None
    shelf_life_days: int | None = None
    case_weight_lb: Decimal | None = None
    commodity_code: str | None = Field(None, description="Produce commodity / FSMA Food Traceability List category")


class Price(CdmModel):
    amount: Decimal = Field(description="Unit price per pricing UOM")
    currency: str = "USD"
    basis_uom: str | None = Field(None, description="Pricing unit (PO105 basis)")
    source: Literal["CUSTOMER_AGREEMENT", "LIST", "PURCHASE_ORDER", "MANUAL_OVERRIDE"] = "PURCHASE_ORDER"
    agreement_id: str | None = None
    agreement_price: Decimal | None = Field(None, description="Price per the trade agreement at validation time")
    list_price: Decimal | None = None


class AllowanceCharge(CdmModel):
    indicator: Literal["ALLOWANCE", "CHARGE"]
    code: str = Field(description="X12 SAC02 code e.g. D240 freight, C310 discount, F050 fuel")
    description: str | None = None
    amount: Decimal | None = None
    percent: Decimal | None = None
    line_number: int | None = None


class TaxAmount(CdmModel):
    tax_type: str = "SALES"
    jurisdiction: str | None = None
    rate: Decimal | None = None
    amount: Decimal


class LineQuantities(CdmModel):
    ordered: Decimal = Decimal(0)
    confirmed: Decimal = Decimal(0)
    backordered: Decimal = Decimal(0)
    allocated: Decimal = Decimal(0)
    picked: Decimal = Decimal(0)
    shipped: Decimal = Decimal(0)
    delivered: Decimal = Decimal(0)
    invoiced: Decimal = Decimal(0)
    cancelled: Decimal = Decimal(0)


class LotAllocation(CdmModel):
    lot_number: str
    quantity: Decimal
    traceability_lot_code: str | None = Field(None, description="FSMA 204 Traceability Lot Code (TLC)")
    pack_date: date | None = None
    harvest_date: date | None = None
    best_by_date: date | None = None
    country_of_origin: str | None = None


class OrderLine(CdmModel):
    line_number: int = Field(ge=1, description="Data product line number")
    buyer_line_number: str | None = Field(None, description="PO101 assigned identification")
    erp_line_id: str | None = Field(None, description="D365 LineCreationSequenceNumber / InventTransId")
    item: ItemReference
    quantity: Quantity = Field(description="Ordered quantity in the customer's unit of measure")
    quantities: LineQuantities = Field(default_factory=LineQuantities,
                                       description="Running quantities across the lifecycle")
    unit_price: Price | None = None
    line_amount: Decimal | None = None
    allowances_charges: list[AllowanceCharge] = Field(default_factory=list)
    taxes: list[TaxAmount] = Field(default_factory=list)
    requested_delivery_date: date | None = None
    requested_ship_date: date | None = None
    promised_ship_date: date | None = None
    status: LineStatus = LineStatus.OPEN
    substitution_allowed: bool = False
    substituted_item_number: str | None = None
    min_remaining_shelf_life_days: int | None = None
    lots: list[LotAllocation] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)


class Totals(CdmModel):
    line_count: int = 0
    total_quantity: Decimal = Decimal(0)
    subtotal: Decimal = Decimal(0)
    allowances: Decimal = Decimal(0)
    charges: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    grand_total: Decimal = Decimal(0)
    currency: str = "USD"
    total_weight_lb: Decimal | None = None


# --------------------------------------------------------------------------- fulfillment
class ShipmentLine(CdmModel):
    line_number: int
    item_number: str | None = None
    quantity: Decimal
    uom: str | None = None
    lots: list[LotAllocation] = Field(default_factory=list)


class ProofOfDelivery(CdmModel):
    received_by: str | None = None
    received_at: datetime | None = None
    exceptions: list[str] = Field(default_factory=list, description="OS&D: over, short, damaged, temperature")


class Shipment(CdmModel):
    shipment_id: str
    asn_number: str | None = Field(None, description="856 BSN02 shipment identification")
    erp_packing_slip_id: str | None = None
    carrier_scac: str | None = None
    carrier_name: str | None = None
    tracking_number: str | None = None
    bill_of_lading: str | None = None
    pro_number: str | None = None
    trailer_number: str | None = None
    seal_number: str | None = None
    ship_from_warehouse: str | None = None
    shipped_at: datetime | None = None
    estimated_delivery_at: datetime | None = None
    delivered_at: datetime | None = None
    status: Literal["PLANNED", "IN_TRANSIT", "DELIVERED", "EXCEPTION"] = "IN_TRANSIT"
    sscc: list[str] = Field(default_factory=list, description="GS1 SSCC pallet labels")
    temperature_log_ref: str | None = None
    proof_of_delivery: ProofOfDelivery | None = None
    lines: list[ShipmentLine] = Field(default_factory=list)


class InvoiceLine(CdmModel):
    line_number: int
    quantity: Decimal
    unit_price: Decimal | None = None
    amount: Decimal


class Invoice(CdmModel):
    invoice_id: str
    invoice_date: date
    due_date: date | None = None
    amount: Decimal
    tax_amount: Decimal = Decimal(0)
    currency: str = "USD"
    status: Literal["POSTED", "SENT", "PAID", "CREDITED"] = "POSTED"
    lines: list[InvoiceLine] = Field(default_factory=list)


class Acknowledgment(CdmModel):
    type: AckType
    document_id: str | None = None
    control_number: str | None = None
    status: Literal["ACCEPTED", "ACCEPTED_WITH_CHANGES", "REJECTED", "SENT"] = "SENT"
    channel: str | None = None
    recipient: str | None = None
    sent_at: datetime


# --------------------------------------------------------------------------- quality / exceptions
class RuleResult(CdmModel):
    rule_id: str = Field(description="DQ-001 .. DQ-011")
    name: str
    passed: bool
    severity: Severity = Severity.ERROR
    message: str | None = None
    line_number: int | None = None
    field: str | None = None


class DataQuality(CdmModel):
    rule_set_version: str = "1.0"
    validated_at: datetime | None = None
    attempt: int = 0
    passed: bool = False
    first_pass: bool | None = Field(None, description="True when the first validation attempt passed every rule")
    score: float = Field(0, description="Share of rule checks passed (0-1)")
    results: list[RuleResult] = Field(default_factory=list)


class OrderException(CdmModel):
    exception_id: str
    code: str = Field(description="Machine-readable reason code, e.g. PRICE_MISMATCH")
    rule_id: str | None = None
    severity: Severity = Severity.ERROR
    message: str
    line_number: int | None = None
    field: str | None = None
    detected_at: datetime
    detected_by: str = "process-api"
    state: ExceptionState = ExceptionState.OPEN
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


# --------------------------------------------------------------------------- ROI tags (deck slide 4)
class StageTimestamps(CdmModel):
    received: datetime | None = None
    ingested: datetime | None = None
    validated: datetime | None = None
    enriched: datetime | None = None
    published: datetime | None = None
    submitted_to_erp: datetime | None = None
    erp_created: datetime | None = None
    released: datetime | None = None
    picked: datetime | None = None
    packed: datetime | None = None
    shipped: datetime | None = None
    delivered: datetime | None = None
    invoiced: datetime | None = None
    closed: datetime | None = None
    cancelled: datetime | None = None


class AckTimestamps(CdmModel):
    functional_ack_997: datetime | None = None
    po_ack_855: datetime | None = None
    ship_notice_856: datetime | None = None
    invoice_810: datetime | None = None


class ManualTouch(CdmModel):
    at: datetime
    actor: str
    action: str
    minutes: float | None = Field(None, description="Estimated handling minutes for labor ROI")


class CycleTimes(CdmModel):
    receipt_to_erp_minutes: float | None = None
    receipt_to_ship_hours: float | None = None
    receipt_to_invoice_hours: float | None = None
    ship_to_asn_minutes: float | None = Field(None, description="Chargeback driver: ASN latency after ship")


class RoiTags(CdmModel):
    source_channel: SourceChannel
    touchless: bool = True
    touch_count: int = 0
    manual_touches: list[ManualTouch] = Field(default_factory=list)
    exception_reason_codes: list[str] = Field(default_factory=list)
    stage_timestamps: StageTimestamps = Field(default_factory=StageTimestamps)
    acknowledgment_timestamps: AckTimestamps = Field(default_factory=AckTimestamps)
    cycle_times: CycleTimes = Field(default_factory=CycleTimes)
    chargeback_risk: list[str] = Field(default_factory=list, description="LATE_855, LATE_856, ASN_MISMATCH ...")
    registered_consumers: list[str] = Field(default_factory=list, description="Output-port consumers that received the order")


# --------------------------------------------------------------------------- lifecycle history
class LineChange(CdmModel):
    line_number: int
    field: str
    from_value: Any = None
    to_value: Any = None


class PatchOperation(CdmModel):
    op: Literal["add", "remove", "replace"]
    path: str
    value: Any = None
    from_value: Any = Field(None, alias="fromValue")


class OrderHistoryEntry(CdmModel):
    """One immutable entry in the lifecycle of an order (append-only, ordered by `sequence`)."""
    event_id: str
    order_id: str
    order_group_id: str | None = None
    sequence: int = Field(ge=1, description="Monotonic per order")
    event_type: HistoryEventType
    occurred_at: datetime = Field(description="When it happened in the source system")
    recorded_at: datetime = Field(description="When the data product recorded it")
    from_status: OrderStatus | None = None
    to_status: OrderStatus | None = None
    status_category: StatusCategory | None = None
    tier: Tier = Tier.TIER2
    source_system: str = Field(description="EDI-GATEWAY, EMAIL, PORTAL, PROCESS-API, D365, WMS, TMS, CRM ...")
    actor: Actor
    correlation_id: str | None = None
    causation_id: str | None = Field(None, description="Event or command that caused this entry")
    external_event_id: str | None = Field(None, description="Idempotency key from the source system")
    reason_code: str | None = None
    message: str | None = None
    line_changes: list[LineChange] = Field(default_factory=list)
    changes: list[PatchOperation] = Field(default_factory=list, description="JSON-Patch style diff of the canonical order")
    document_ref: str | None = None
    elapsed_since_previous_seconds: float | None = None


class RelatedOrder(CdmModel):
    order_id: str
    relationship: RelationshipType
    note: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- the canonical order
class SalesOrder(CdmModel):
    """Canonical Sales Order - the published record of the Sales Order data product."""
    schema_version: str = CDM_VERSION
    order_id: str = Field(description="Data product order id: SO-YYYYMMDD-XXXXXXXX")
    order_group_id: str = Field(description="Groups orders that arrived together (one interchange/email) or were split/replaced")
    version: int = Field(1, description="Optimistic concurrency version; increments on every change")
    order_type: OrderType = OrderType.STANDARD
    purpose: OrderPurpose = OrderPurpose.ORIGINAL
    status: OrderStatus
    status_category: StatusCategory
    status_reason: str | None = None
    status_updated_at: datetime
    source: SourceInfo
    purchase_order: PurchaseOrderReference
    erp: ErpReference | None = None
    parties: Parties
    dates: OrderDates = Field(default_factory=OrderDates)
    commercial: Commercial = Field(default_factory=Commercial)
    shipping: ShippingInstructions = Field(default_factory=ShippingInstructions)
    fulfillment: Fulfillment | None = Field(None, description="Regional network fulfilment and sourcing decision")
    lines: list[OrderLine] = Field(min_length=0)
    allowances_charges: list[AllowanceCharge] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    references: list[Reference] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    shipments: list[Shipment] = Field(default_factory=list)
    invoices: list[Invoice] = Field(default_factory=list)
    acknowledgments: list[Acknowledgment] = Field(default_factory=list)
    exceptions: list[OrderException] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    roi: RoiTags
    related_orders: list[RelatedOrder] = Field(default_factory=list)
    order_history: list[OrderHistoryEntry] = Field(default_factory=list,
                                                   description="Complete, ordered lifecycle of this order")
    audit: AuditInfo
    extensions: dict[str, Any] = Field(default_factory=dict, description="Namespaced, non-contract attributes")


class OrderGroup(CdmModel):
    """Lifecycle across orders that belong together (multi-ST interchange, split, backorder, replacement)."""
    order_group_id: str
    origin: str | None = Field(None, description="e.g. ISA13 control number or email Message-ID")
    order_ids: list[str]
    aggregate_status_category: StatusCategory | None = None
    created_at: datetime
    history: list[OrderHistoryEntry] = Field(default_factory=list)


class OrderStatusChanged(CdmModel):
    """`data` payload of the sales-order-events CloudEvent (output port: order status events)."""
    order_id: str
    order_group_id: str | None = None
    sequence: int
    event_type: HistoryEventType
    from_status: OrderStatus | None = None
    to_status: OrderStatus | None = None
    status_category: StatusCategory | None = None
    occurred_at: datetime
    source_channel: SourceChannel
    customer_id: str | None = None
    customer_po: str | None = None
    erp_sales_order_id: str | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    roi: dict[str, Any] | None = None


class StatusEvent(CdmModel):
    """Inbound status event from a Tier-3 system (system-status-events topic / POST /v1/events)."""
    event_id: str
    event_type: str = Field(description="ERP_ACCEPTED, ERP_REJECTED, CREDIT_HOLD_PLACED, RELEASED_TO_WAREHOUSE, PICKED, PACKED, SHIPPED, DELIVERED, INVOICED, CLOSED, CANCELLED")
    order_id: str | None = None
    erp_sales_order_id: str | None = None
    occurred_at: datetime
    source_system: str
    reason: str | None = None
    erp: ErpReference | None = None
    lines: list[dict[str, Any]] = Field(default_factory=list,
                                        description="[{lineNumber, quantity, status, confirmedQuantity, backorderedQuantity, erpLineId, lots[]}]")
    shipment: Shipment | None = None
    invoice: Invoice | None = None
    promised_ship_date: date | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
