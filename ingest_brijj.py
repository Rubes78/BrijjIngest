#!/usr/bin/env python3
"""
BrijjData ingestion script
Pulls RcsItems and Sales from the Brijj API suite and upserts them into
the BrijjData SQL Server database.

All credentials and endpoints are read from config.ini.
Run configure.py to set or update the configuration.
"""

import configparser
import datetime
import math
import os
import sys

import requests
import pyodbc

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.ini")


def load_config() -> configparser.ConfigParser:
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: config.ini not found at {CONFIG_FILE}")
        print("Run configure.py to create it.")
        sys.exit(1)
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    return cfg


def get_companies(cfg: configparser.ConfigParser) -> list:
    companies = []
    for section in cfg.sections():
        if not section.startswith("company:"):
            continue
        sec = cfg[section]
        if sec.get("enabled", "false").lower() != "true":
            continue
        int_id_raw = sec.get("company_int_id", "").strip()
        db_name = sec.get("db_name", "").strip()
        if not db_name:
            print(f"WARNING: Company {sec.get('company_id')} has no db_name set — skipping.")
            continue
        companies.append({
            "companyID":    sec["company_id"],
            "companyIntId": int(int_id_raw) if int_id_raw else None,
            "userName":     sec["username"],
            "password":     sec["password"],
            "dbName":       db_name,
        })
    return companies

# ── DDL ──────────────────────────────────────────────────────────────────────

CREATE_TABLE_RCS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'rcs_items')
CREATE TABLE rcs_items (
    id                   BIGINT IDENTITY(1,1) PRIMARY KEY,
    barcode              NVARCHAR(50)    NOT NULL,
    companyID            NVARCHAR(50)    NOT NULL,
    itemID               INT,
    lotID                INT,
    numberofPieces       INT,
    productionStoreID    INT,
    productionStoreName  NVARCHAR(255),
    transferStoreID      INT,
    storeName            NVARCHAR(255),
    stationID            INT,
    stationName          NVARCHAR(255),
    processorID          INT,
    processorName        NVARCHAR(255),
    qtyProduced          INT,
    qtySold              INT,
    onHand               INT,
    lastUpdatedDt        DATETIME2,
    departmentID         INT,
    department           NVARCHAR(255),
    categoryID           INT,
    categoryName         NVARCHAR(255),
    productionCycleID    INT,
    productionCycle      NVARCHAR(255),
    qualityID            INT,
    qualityDescr         NVARCHAR(255),
    conditionID          INT,
    conditionDescr       NVARCHAR(255),
    productionStoreCode  NVARCHAR(50),
    transferStoreCode    NVARCHAR(50),
    price                DECIMAL(10, 2),
    subcategoryID        INT,
    subcategoryName      NVARCHAR(255),
    subcategoryExtId     NVARCHAR(100),
    departmentExtId      NVARCHAR(100),
    categoryExtId        NVARCHAR(100),
    isScanPull           BIT,
    ingested_at          DATETIME2 DEFAULT GETDATE(),
    CONSTRAINT uq_barcode_company UNIQUE (barcode, companyID)
);
"""

CREATE_TABLE_SALES_ORDERS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'sales_orders')
CREATE TABLE sales_orders (
    salesOrderId                    BIGINT PRIMARY KEY,
    companyId                       INT,
    userId                          INT,
    userName                        NVARCHAR(255),
    salesType                       NVARCHAR(100),
    referenceNumber                 NVARCHAR(100),
    referenceSalesOrderId           BIGINT,
    date                            DATE,
    createdAt                       DATETIMEOFFSET,
    totalAmount                     DECIMAL(12, 4),
    discountAmount                  DECIMAL(12, 4),
    totalDiscountPercentageOnOrder  DECIMAL(10, 4),
    orderTotalBeforeDiscount        DECIMAL(12, 4),
    taxAmount                       DECIMAL(12, 4),
    roundupAmount                   DECIMAL(12, 4),
    checkoutType                    NVARCHAR(100),
    returnType                      NVARCHAR(100),
    loyaltyPoints                   INT,
    note                            NVARCHAR(MAX),
    paymentStatus                   NVARCHAR(100),
    customerId                      INT,
    customerFirstName               NVARCHAR(255),
    customerLastName                NVARCHAR(255),
    customerLoyaltyPoints           DECIMAL(12, 4),
    customerStoreCredit             DECIMAL(12, 4),
    storeId                         INT,
    storeName                       NVARCHAR(255),
    registerId                      INT,
    registerName                    NVARCHAR(255),
    ingested_at                     DATETIME2 DEFAULT GETDATE()
);
"""

CREATE_TABLE_SALES_ITEMS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'sales_order_items')
CREATE TABLE sales_order_items (
    salesOrderItemId            BIGINT PRIMARY KEY,
    salesOrderId                BIGINT,
    productId                   INT,
    productName                 NVARCHAR(255),
    productSKU                  NVARCHAR(100),
    tpmProductId                BIGINT,
    tpmProductName              NVARCHAR(255),
    quantity                    INT,
    returnedQuantity            INT,
    returnedAmount              DECIMAL(12, 4),
    availableToReturnQuantity   INT,
    sellingPrice                DECIMAL(12, 4),
    totalAmount                 DECIMAL(12, 4),
    subtotalAmount              DECIMAL(12, 4),
    discountAmount              DECIMAL(12, 4),
    taxAmount                   DECIMAL(12, 4),
    isReturnable                BIT,
    discountType                NVARCHAR(100),
    discountValue               DECIMAL(10, 4),
    taxValueType                NVARCHAR(100),
    taxValue                    DECIMAL(10, 4),
    salesOrderItemType          NVARCHAR(100),
    productCondition            NVARCHAR(255),
    offerProductId              INT,
    originalProductId           INT,
    note                        NVARCHAR(MAX),
    promoDiscountAmount         DECIMAL(12, 4),
    ingested_at                 DATETIME2 DEFAULT GETDATE()
);
"""

CREATE_TABLE_SALES_PAYMENTS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'sales_payments')
CREATE TABLE sales_payments (
    id              BIGINT PRIMARY KEY,
    salesOrderId    BIGINT,
    paymentStatus   NVARCHAR(100),
    currency        NVARCHAR(20),
    paymentMethod   NVARCHAR(100),
    amount          DECIMAL(12, 4),
    ingested_at     DATETIME2 DEFAULT GETDATE()
);
"""

CREATE_TABLE_CUSTOMERS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'customers')
CREATE TABLE customers (
    id                           INT PRIMARY KEY,
    companyId                    INT,
    firstName                    NVARCHAR(255),
    lastName                     NVARCHAR(255),
    email                        NVARCHAR(255),
    phoneNumber                  NVARCHAR(100),
    gender                       INT,
    dateOfBirth                  DATE,
    preferredLanguageId          INT,
    homeStoreId                  INT,
    homeStoreName                NVARCHAR(255),
    note                         NVARCHAR(MAX),
    allowToSendPromotionalEmails BIT,
    accountBalanceOption         INT,
    enableLoyaltyProgram         BIT,
    externalId                   NVARCHAR(255),
    createdAt                    DATETIMEOFFSET,
    loyaltyPoints                DECIMAL(12, 4),
    storeCredit                  DECIMAL(12, 4),
    onAccountBalance             DECIMAL(12, 4),
    enableTaxExemption           BIT,
    taxExemptionId               NVARCHAR(255),
    taxExemptionType             INT,
    addressLine1                 NVARCHAR(255),
    addressLine2                 NVARCHAR(255),
    addressCity                  NVARCHAR(255),
    addressZipCode               NVARCHAR(50),
    addressStateName             NVARCHAR(255),
    addressCountryName           NVARCHAR(255),
    addressLatitude              DECIMAL(10, 7),
    addressLongitude             DECIMAL(10, 7),
    addressType                  INT,
    ingested_at                  DATETIME2 DEFAULT GETDATE()
);
"""

CREATE_TABLE_CUSTOMER_GROUPS = """
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'customer_groups')
CREATE TABLE customer_groups (
    groupId         INT          NOT NULL,
    customerId      INT          NOT NULL,
    groupName       NVARCHAR(255),
    description     NVARCHAR(MAX),
    companyId       INT,
    groupCreatedAt  DATETIMEOFFSET,
    ingested_at     DATETIME2 DEFAULT GETDATE(),
    PRIMARY KEY (customerId, groupId)
);
"""

# ── RCS SQL ───────────────────────────────────────────────────────────────────

CREATE_TEMP_RCS = """
CREATE TABLE #rcs_staging (
    barcode              NVARCHAR(50),
    companyID            NVARCHAR(50),
    itemID               INT,
    lotID                INT,
    numberofPieces       INT,
    productionStoreID    INT,
    productionStoreName  NVARCHAR(255),
    transferStoreID      INT,
    storeName            NVARCHAR(255),
    stationID            INT,
    stationName          NVARCHAR(255),
    processorID          INT,
    processorName        NVARCHAR(255),
    qtyProduced          INT,
    qtySold              INT,
    onHand               INT,
    lastUpdatedDt        DATETIME2,
    departmentID         INT,
    department           NVARCHAR(255),
    categoryID           INT,
    categoryName         NVARCHAR(255),
    productionCycleID    INT,
    productionCycle      NVARCHAR(255),
    qualityID            INT,
    qualityDescr         NVARCHAR(255),
    conditionID          INT,
    conditionDescr       NVARCHAR(255),
    productionStoreCode  NVARCHAR(50),
    transferStoreCode    NVARCHAR(50),
    price                DECIMAL(10,2),
    subcategoryID        INT,
    subcategoryName      NVARCHAR(255),
    subcategoryExtId     NVARCHAR(100),
    departmentExtId      NVARCHAR(100),
    categoryExtId        NVARCHAR(100),
    isScanPull           BIT
)
"""

INSERT_RCS_STAGING = """
INSERT INTO #rcs_staging (
    barcode, companyID, itemID, lotID, numberofPieces,
    productionStoreID, productionStoreName, transferStoreID, storeName,
    stationID, stationName, processorID, processorName,
    qtyProduced, qtySold, onHand, lastUpdatedDt,
    departmentID, department, categoryID, categoryName,
    productionCycleID, productionCycle,
    qualityID, qualityDescr, conditionID, conditionDescr,
    productionStoreCode, transferStoreCode, price,
    subcategoryID, subcategoryName, subcategoryExtId,
    departmentExtId, categoryExtId, isScanPull
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?
)
"""

MERGE_RCS = """
MERGE rcs_items AS target
USING #rcs_staging AS source
ON target.barcode = source.barcode AND target.companyID = source.companyID
WHEN MATCHED THEN UPDATE SET
    itemID              = source.itemID,
    lotID               = source.lotID,
    numberofPieces      = source.numberofPieces,
    productionStoreID   = source.productionStoreID,
    productionStoreName = source.productionStoreName,
    transferStoreID     = source.transferStoreID,
    storeName           = source.storeName,
    stationID           = source.stationID,
    stationName         = source.stationName,
    processorID         = source.processorID,
    processorName       = source.processorName,
    qtyProduced         = source.qtyProduced,
    qtySold             = source.qtySold,
    onHand              = source.onHand,
    lastUpdatedDt       = source.lastUpdatedDt,
    departmentID        = source.departmentID,
    department          = source.department,
    categoryID          = source.categoryID,
    categoryName        = source.categoryName,
    productionCycleID   = source.productionCycleID,
    productionCycle     = source.productionCycle,
    qualityID           = source.qualityID,
    qualityDescr        = source.qualityDescr,
    conditionID         = source.conditionID,
    conditionDescr      = source.conditionDescr,
    productionStoreCode = source.productionStoreCode,
    transferStoreCode   = source.transferStoreCode,
    price               = source.price,
    subcategoryID       = source.subcategoryID,
    subcategoryName     = source.subcategoryName,
    subcategoryExtId    = source.subcategoryExtId,
    departmentExtId     = source.departmentExtId,
    categoryExtId       = source.categoryExtId,
    isScanPull          = source.isScanPull,
    ingested_at         = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    barcode, companyID, itemID, lotID, numberofPieces,
    productionStoreID, productionStoreName, transferStoreID, storeName,
    stationID, stationName, processorID, processorName,
    qtyProduced, qtySold, onHand, lastUpdatedDt,
    departmentID, department, categoryID, categoryName,
    productionCycleID, productionCycle,
    qualityID, qualityDescr, conditionID, conditionDescr,
    productionStoreCode, transferStoreCode, price,
    subcategoryID, subcategoryName, subcategoryExtId,
    departmentExtId, categoryExtId, isScanPull
) VALUES (
    source.barcode, source.companyID, source.itemID, source.lotID, source.numberofPieces,
    source.productionStoreID, source.productionStoreName, source.transferStoreID, source.storeName,
    source.stationID, source.stationName, source.processorID, source.processorName,
    source.qtyProduced, source.qtySold, source.onHand, source.lastUpdatedDt,
    source.departmentID, source.department, source.categoryID, source.categoryName,
    source.productionCycleID, source.productionCycle,
    source.qualityID, source.qualityDescr, source.conditionID, source.conditionDescr,
    source.productionStoreCode, source.transferStoreCode, source.price,
    source.subcategoryID, source.subcategoryName, source.subcategoryExtId,
    source.departmentExtId, source.categoryExtId, source.isScanPull
);
"""

# ── Sales SQL ─────────────────────────────────────────────────────────────────

UPSERT_SALES_ORDER = """
MERGE sales_orders AS target
USING (VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)) AS source (
    salesOrderId, companyId, userId, userName, salesType,
    referenceNumber, referenceSalesOrderId, date, createdAt,
    totalAmount, discountAmount, totalDiscountPercentageOnOrder,
    orderTotalBeforeDiscount, taxAmount, roundupAmount,
    checkoutType, returnType, loyaltyPoints,
    note, paymentStatus,
    customerId, customerFirstName, customerLastName, customerLoyaltyPoints, customerStoreCredit,
    storeId, storeName, registerId, registerName
)
ON target.salesOrderId = source.salesOrderId
WHEN MATCHED THEN UPDATE SET
    companyId                      = source.companyId,
    userId                         = source.userId,
    userName                       = source.userName,
    salesType                      = source.salesType,
    referenceNumber                = source.referenceNumber,
    referenceSalesOrderId          = source.referenceSalesOrderId,
    date                           = source.date,
    createdAt                      = source.createdAt,
    totalAmount                    = source.totalAmount,
    discountAmount                 = source.discountAmount,
    totalDiscountPercentageOnOrder = source.totalDiscountPercentageOnOrder,
    orderTotalBeforeDiscount       = source.orderTotalBeforeDiscount,
    taxAmount                      = source.taxAmount,
    roundupAmount                  = source.roundupAmount,
    checkoutType                   = source.checkoutType,
    returnType                     = source.returnType,
    loyaltyPoints                  = source.loyaltyPoints,
    note                           = source.note,
    paymentStatus                  = source.paymentStatus,
    customerId                     = source.customerId,
    customerFirstName              = source.customerFirstName,
    customerLastName               = source.customerLastName,
    customerLoyaltyPoints          = source.customerLoyaltyPoints,
    customerStoreCredit            = source.customerStoreCredit,
    storeId                        = source.storeId,
    storeName                      = source.storeName,
    registerId                     = source.registerId,
    registerName                   = source.registerName,
    ingested_at                    = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    salesOrderId, companyId, userId, userName, salesType,
    referenceNumber, referenceSalesOrderId, date, createdAt,
    totalAmount, discountAmount, totalDiscountPercentageOnOrder,
    orderTotalBeforeDiscount, taxAmount, roundupAmount,
    checkoutType, returnType, loyaltyPoints,
    note, paymentStatus,
    customerId, customerFirstName, customerLastName, customerLoyaltyPoints, customerStoreCredit,
    storeId, storeName, registerId, registerName
) VALUES (
    source.salesOrderId, source.companyId, source.userId, source.userName, source.salesType,
    source.referenceNumber, source.referenceSalesOrderId, source.date, source.createdAt,
    source.totalAmount, source.discountAmount, source.totalDiscountPercentageOnOrder,
    source.orderTotalBeforeDiscount, source.taxAmount, source.roundupAmount,
    source.checkoutType, source.returnType, source.loyaltyPoints,
    source.note, source.paymentStatus,
    source.customerId, source.customerFirstName, source.customerLastName,
    source.customerLoyaltyPoints, source.customerStoreCredit,
    source.storeId, source.storeName, source.registerId, source.registerName
);
"""

UPSERT_SALES_ITEM = """
MERGE sales_order_items AS target
USING (VALUES (
    ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?
)) AS source (
    salesOrderItemId, salesOrderId, productId, productName, productSKU,
    tpmProductId, tpmProductName,
    quantity, returnedQuantity, returnedAmount, availableToReturnQuantity,
    sellingPrice, totalAmount, subtotalAmount,
    discountAmount, taxAmount, isReturnable,
    discountType, discountValue, taxValueType, taxValue,
    salesOrderItemType, productCondition, offerProductId, originalProductId,
    note, promoDiscountAmount
)
ON target.salesOrderItemId = source.salesOrderItemId
WHEN MATCHED THEN UPDATE SET
    salesOrderId              = source.salesOrderId,
    productId                 = source.productId,
    productName               = source.productName,
    productSKU                = source.productSKU,
    tpmProductId              = source.tpmProductId,
    tpmProductName            = source.tpmProductName,
    quantity                  = source.quantity,
    returnedQuantity          = source.returnedQuantity,
    returnedAmount            = source.returnedAmount,
    availableToReturnQuantity = source.availableToReturnQuantity,
    sellingPrice              = source.sellingPrice,
    totalAmount               = source.totalAmount,
    subtotalAmount            = source.subtotalAmount,
    discountAmount            = source.discountAmount,
    taxAmount                 = source.taxAmount,
    isReturnable              = source.isReturnable,
    discountType              = source.discountType,
    discountValue             = source.discountValue,
    taxValueType              = source.taxValueType,
    taxValue                  = source.taxValue,
    salesOrderItemType        = source.salesOrderItemType,
    productCondition          = source.productCondition,
    offerProductId            = source.offerProductId,
    originalProductId         = source.originalProductId,
    note                      = source.note,
    promoDiscountAmount       = source.promoDiscountAmount,
    ingested_at               = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    salesOrderItemId, salesOrderId, productId, productName, productSKU,
    tpmProductId, tpmProductName,
    quantity, returnedQuantity, returnedAmount, availableToReturnQuantity,
    sellingPrice, totalAmount, subtotalAmount,
    discountAmount, taxAmount, isReturnable,
    discountType, discountValue, taxValueType, taxValue,
    salesOrderItemType, productCondition, offerProductId, originalProductId,
    note, promoDiscountAmount
) VALUES (
    source.salesOrderItemId, source.salesOrderId, source.productId, source.productName, source.productSKU,
    source.tpmProductId, source.tpmProductName,
    source.quantity, source.returnedQuantity, source.returnedAmount, source.availableToReturnQuantity,
    source.sellingPrice, source.totalAmount, source.subtotalAmount,
    source.discountAmount, source.taxAmount, source.isReturnable,
    source.discountType, source.discountValue, source.taxValueType, source.taxValue,
    source.salesOrderItemType, source.productCondition, source.offerProductId, source.originalProductId,
    source.note, source.promoDiscountAmount
);
"""

UPSERT_SALES_PAYMENT = """
MERGE sales_payments AS target
USING (VALUES (?, ?, ?, ?, ?, ?)) AS source (
    id, salesOrderId, paymentStatus, currency, paymentMethod, amount
)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET
    salesOrderId  = source.salesOrderId,
    paymentStatus = source.paymentStatus,
    currency      = source.currency,
    paymentMethod = source.paymentMethod,
    amount        = source.amount,
    ingested_at   = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    id, salesOrderId, paymentStatus, currency, paymentMethod, amount
) VALUES (
    source.id, source.salesOrderId, source.paymentStatus,
    source.currency, source.paymentMethod, source.amount
);
"""

# ── Customers SQL ─────────────────────────────────────────────────────────────

UPSERT_CUSTOMER = """
MERGE customers AS target
USING (VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)) AS source (
    id, companyId, firstName, lastName, email, phoneNumber,
    gender, dateOfBirth, preferredLanguageId,
    homeStoreId, homeStoreName, note,
    allowToSendPromotionalEmails, accountBalanceOption, enableLoyaltyProgram,
    externalId, createdAt, loyaltyPoints, storeCredit, onAccountBalance,
    enableTaxExemption, taxExemptionId, taxExemptionType,
    addressLine1, addressLine2, addressCity, addressZipCode,
    addressStateName, addressCountryName, addressLatitude, addressLongitude
)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET
    companyId                    = source.companyId,
    firstName                    = source.firstName,
    lastName                     = source.lastName,
    email                        = source.email,
    phoneNumber                  = source.phoneNumber,
    gender                       = source.gender,
    dateOfBirth                  = source.dateOfBirth,
    preferredLanguageId          = source.preferredLanguageId,
    homeStoreId                  = source.homeStoreId,
    homeStoreName                = source.homeStoreName,
    note                         = source.note,
    allowToSendPromotionalEmails = source.allowToSendPromotionalEmails,
    accountBalanceOption         = source.accountBalanceOption,
    enableLoyaltyProgram         = source.enableLoyaltyProgram,
    externalId                   = source.externalId,
    createdAt                    = source.createdAt,
    loyaltyPoints                = source.loyaltyPoints,
    storeCredit                  = source.storeCredit,
    onAccountBalance             = source.onAccountBalance,
    enableTaxExemption           = source.enableTaxExemption,
    taxExemptionId               = source.taxExemptionId,
    taxExemptionType             = source.taxExemptionType,
    addressLine1                 = source.addressLine1,
    addressLine2                 = source.addressLine2,
    addressCity                  = source.addressCity,
    addressZipCode               = source.addressZipCode,
    addressStateName             = source.addressStateName,
    addressCountryName           = source.addressCountryName,
    addressLatitude              = source.addressLatitude,
    addressLongitude             = source.addressLongitude,
    ingested_at                  = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    id, companyId, firstName, lastName, email, phoneNumber,
    gender, dateOfBirth, preferredLanguageId,
    homeStoreId, homeStoreName, note,
    allowToSendPromotionalEmails, accountBalanceOption, enableLoyaltyProgram,
    externalId, createdAt, loyaltyPoints, storeCredit, onAccountBalance,
    enableTaxExemption, taxExemptionId, taxExemptionType,
    addressLine1, addressLine2, addressCity, addressZipCode,
    addressStateName, addressCountryName, addressLatitude, addressLongitude
) VALUES (
    source.id, source.companyId, source.firstName, source.lastName,
    source.email, source.phoneNumber, source.gender, source.dateOfBirth,
    source.preferredLanguageId, source.homeStoreId, source.homeStoreName, source.note,
    source.allowToSendPromotionalEmails, source.accountBalanceOption, source.enableLoyaltyProgram,
    source.externalId, source.createdAt, source.loyaltyPoints, source.storeCredit,
    source.onAccountBalance, source.enableTaxExemption, source.taxExemptionId, source.taxExemptionType,
    source.addressLine1, source.addressLine2, source.addressCity, source.addressZipCode,
    source.addressStateName, source.addressCountryName, source.addressLatitude, source.addressLongitude
);
"""

UPSERT_CUSTOMER_GROUP = """
MERGE customer_groups AS target
USING (VALUES (?, ?, ?, ?, ?, ?)) AS source (
    groupId, customerId, groupName, description, companyId, groupCreatedAt
)
ON target.customerId = source.customerId AND target.groupId = source.groupId
WHEN MATCHED THEN UPDATE SET
    groupName      = source.groupName,
    description    = source.description,
    companyId      = source.companyId,
    groupCreatedAt = source.groupCreatedAt,
    ingested_at    = GETDATE()
WHEN NOT MATCHED THEN INSERT (
    groupId, customerId, groupName, description, companyId, groupCreatedAt
) VALUES (
    source.groupId, source.customerId, source.groupName, source.description,
    source.companyId, source.groupCreatedAt
);
"""

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token(auth_base: str, company: dict) -> str:
    resp = requests.post(
        f"{auth_base}/auth/signin",
        json={"userName": company["userName"], "password": company["password"]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not company.get("companyIntId"):
        company["companyIntId"] = data["company"]["id"]
    return data["accessToken"]

# ── RCS ───────────────────────────────────────────────────────────────────────

def fetch_rcs_items(rcs_base: str, company: dict,
                    start_date: str, end_date: str) -> list:
    resp = requests.get(
        f"{rcs_base}/RcsItems",
        params={
            "userName":      company["userName"],
            "password":      company["password"],
            "companyID":     company["companyID"],
            "Updatestartdt": f"{start_date} 00:00:00",
            "Updateenddt":   f"{end_date} 23:59:59",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def normalize_rcs(record: dict, company_id: str) -> tuple:
    return (
        record.get("barcode"),
        company_id,
        record.get("itemID"),
        record.get("lotID"),
        record.get("numberofPieces"),
        record.get("productionStoreID"),
        record.get("productionStoreName"),
        record.get("transferStoreID"),
        record.get("storeName"),
        record.get("stationID"),
        record.get("stationName"),
        record.get("processorID"),
        record.get("processorName"),
        record.get("qtyProduced"),
        record.get("qtySold"),
        record.get("onHand"),
        record.get("lastUpdatedDt"),
        record.get("departmentID"),
        record.get("department"),
        record.get("categoryID"),
        record.get("categoryName"),
        record.get("productionCycleID"),
        record.get("productionCycle"),
        record.get("qualityID"),
        record.get("qualityDescr"),
        record.get("conditionID"),
        record.get("conditionDescr"),
        record.get("productionStoreCode"),
        record.get("transferStoreCode"),
        record.get("price"),
        record.get("subcateogryID"),   # API typo preserved
        record.get("subcategoryName"),
        record.get("subcategoryExtId"),
        record.get("departmentExtId"),
        record.get("categoryExtId"),
        int(record.get("isScanPull", False)),
    )

# ── Sales ─────────────────────────────────────────────────────────────────────

def fetch_sales_page(sales_base: str, token: str, company_int_id: int,
                     page: int, page_size: int) -> dict:
    resp = requests.get(
        f"{sales_base}/Sales",
        params={"CompanyId": company_int_id, "pageNumber": page, "pageSize": page_size},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def normalize_sales_order(order: dict) -> tuple:
    customer = order.get("customer") or {}
    store    = order.get("store")    or {}
    register = order.get("register") or {}
    return (
        order.get("salesOrderId"),
        order.get("companyId"),
        order.get("userId"),
        order.get("userName"),
        order.get("salesType"),
        order.get("referenceNumber"),
        order.get("referenceSalesOrderId"),
        order.get("date"),
        order.get("createdAt"),
        order.get("totalAmount"),
        order.get("discountAmount"),
        order.get("totalDiscountPercentageOnOrder"),
        order.get("orderTotalBeforeDiscount"),
        order.get("taxAmount"),
        order.get("roundupAmount"),
        order.get("checkoutType"),
        order.get("returnType"),
        order.get("loyaltyPoints"),
        order.get("note"),
        order.get("paymentStatus"),
        customer.get("id"),
        customer.get("firstName"),
        customer.get("lastName"),
        customer.get("loyaltyPoints"),
        customer.get("storeCredit"),
        store.get("id"),
        store.get("name"),
        register.get("id"),
        register.get("name"),
    )


def normalize_sales_item(item: dict, order_id: int) -> tuple:
    return (
        item.get("salesOrderItemId"),
        order_id,
        item.get("productId"),
        item.get("productName"),
        item.get("productSKU"),
        item.get("tpmProductId"),
        item.get("tpmProductName"),
        item.get("quantity"),
        item.get("returnedQuantity"),
        item.get("returnedAmount"),
        item.get("availableToReturnQuantity"),
        item.get("sellingPrice"),
        item.get("totalAmount"),
        item.get("subtotalAmount"),
        item.get("discountAmount"),
        item.get("taxAmount"),
        item.get("isReturnable"),
        item.get("discountType"),
        item.get("discountValue"),
        item.get("taxValueType"),
        item.get("taxValue"),
        item.get("salesOrderItemType"),
        item.get("productCondition"),
        item.get("offerProductId"),
        item.get("originalProductId"),
        item.get("note"),
        item.get("promoDiscountAmount"),
    )


def normalize_sales_payment(payment: dict, order_id: int) -> tuple:
    return (
        payment.get("id"),
        order_id,
        payment.get("paymentStatus"),
        payment.get("currency"),
        payment.get("paymentMethod"),
        payment.get("amount"),
    )

# ── Customers ──────────────────────────────────────────────────────────────────

def fetch_customers_page(sales_base: str, token: str, company_int_id: int,
                         page: int, page_size: int) -> dict:
    resp = requests.get(
        f"{sales_base}/Customers",
        params={"CompanyId": company_int_id, "PageNumber": page, "PageSize": page_size},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def normalize_customer(c: dict) -> tuple:
    addr = c.get("address") or {}
    home = c.get("homeStore") or {}
    return (
        c.get("id"),
        c.get("companyId"),
        c.get("firstName"),
        c.get("lastName"),
        c.get("email"),
        c.get("phoneNumber"),
        c.get("gender"),
        c.get("dateOfBirth"),
        c.get("preferredLanguageId"),
        c.get("homeStoreId"),
        home.get("name") if home else None,
        c.get("note"),
        int(bool(c.get("allowToSendPromotionalEmails"))),
        c.get("accountBalanceOption"),
        int(bool(c.get("enableLoyaltyProgram"))),
        c.get("externalId"),
        c.get("createdAt"),
        c.get("loyaltyPoints"),
        c.get("storeCredit"),
        c.get("onAccountBalance"),
        int(bool(c.get("enableTaxExemption"))),
        c.get("taxExemptionId"),
        c.get("taxExemptionType"),
        addr.get("line1"),
        addr.get("line2"),
        addr.get("city"),
        addr.get("zipCode"),
        addr.get("stateName"),
        addr.get("countryName"),
        addr.get("latitude"),
        addr.get("longitude"),
    )


def upsert_customers_page(conn, customers: list) -> dict:
    cur = conn.cursor()
    counts = {"customers": 0, "groups": 0}
    for c in customers:
        cur.execute(UPSERT_CUSTOMER, normalize_customer(c))
        counts["customers"] += cur.rowcount
        for grp in c.get("customerGroups") or []:
            cur.execute(UPSERT_CUSTOMER_GROUP, (
                grp.get("id"),
                c.get("id"),
                grp.get("name"),
                grp.get("description"),
                grp.get("companyId"),
                grp.get("createdAt"),
            ))
            counts["groups"] += cur.rowcount
    conn.commit()
    cur.close()
    return counts

# ── DB ────────────────────────────────────────────────────────────────────────

def get_connection(cfg: configparser.ConfigParser, database: str = "master"):
    db  = cfg["database"]
    srv = db["server"]
    prt = db.get("port", "1433")
    usr = db["user"]
    pwd = db["password"]
    conn = pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={srv},{prt};DATABASE={database};"
        f"UID={usr};PWD={pwd};"
        f"TrustServerCertificate=yes;",
        timeout=10,
        autocommit=False,
    )
    return conn


def ensure_database(cfg: configparser.ConfigParser, db_name: str):
    conn = get_connection(cfg, database="master")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"IF DB_ID('{db_name}') IS NULL CREATE DATABASE [{db_name}]")
    cur.close()
    conn.close()


def ensure_tables(conn):
    cur = conn.cursor()
    for ddl in [CREATE_TABLE_RCS, CREATE_TABLE_SALES_ORDERS,
                CREATE_TABLE_SALES_ITEMS, CREATE_TABLE_SALES_PAYMENTS,
                CREATE_TABLE_CUSTOMERS, CREATE_TABLE_CUSTOMER_GROUPS]:
        cur.execute(ddl)
    conn.commit()
    cur.close()


def upsert_rcs(conn, records: list) -> int:
    cur = conn.cursor()
    cur.fast_executemany = True   # TDS bulk-copy protocol — orders of magnitude faster

    # Drop staging table if it exists from a prior failed run
    cur.execute("IF OBJECT_ID('tempdb..#rcs_staging') IS NOT NULL DROP TABLE #rcs_staging")
    cur.execute(CREATE_TEMP_RCS)
    cur.execute("CREATE INDEX ix_rcs_stg ON #rcs_staging (barcode, companyID)")
    conn.commit()

    # Bulk-load all rows in one call using the native bulk protocol
    total = len(records)
    print(f"  Bulk loading {total:,} records into staging...", flush=True)
    cur.executemany(INSERT_RCS_STAGING, records)
    conn.commit()

    # Single MERGE from staging → target
    print(f"  Merging into rcs_items...", flush=True)
    cur.execute(MERGE_RCS)
    affected = cur.rowcount
    conn.commit()
    cur.close()
    return affected


def upsert_sales_page(conn, orders: list) -> dict:
    cur = conn.cursor()
    cur.fast_executemany = True
    counts = {"orders": 0, "items": 0, "payments": 0}
    for order in orders:
        order_id = order["salesOrderId"]
        cur.execute(UPSERT_SALES_ORDER, normalize_sales_order(order))
        counts["orders"] += cur.rowcount
        for item in order.get("salesItems") or []:
            cur.execute(UPSERT_SALES_ITEM, normalize_sales_item(item, order_id))
            counts["items"] += cur.rowcount
        for payment in order.get("payments") or []:
            cur.execute(UPSERT_SALES_PAYMENT, normalize_sales_payment(payment, order_id))
            counts["payments"] += cur.rowcount
    conn.commit()
    cur.close()
    return counts

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    today = datetime.date.today().isoformat()
    parser = argparse.ArgumentParser()
    parser.add_argument("--company",    metavar="ID",   help="Run ingestion for a single company ID only")
    parser.add_argument("--start-date", metavar="DATE", default=today,
                        help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--end-date",   metavar="DATE", default=today,
                        help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    start_date = args.start_date
    end_date   = args.end_date
    print(f"Date range: {start_date} 00:00:00  →  {end_date} 23:59:59")

    cfg        = load_config()
    rcs_base   = cfg["apis"]["rcs_base"]
    auth_base  = cfg["apis"]["auth_base"]
    sales_base = cfg["apis"]["sales_base"]
    page_size  = int(cfg["apis"].get("sales_page_size", 1000))
    companies  = get_companies(cfg)

    if args.company:
        companies = [c for c in companies if c["companyID"].upper() == args.company.upper()]
        if not companies:
            print(f"ERROR: Company '{args.company}' not found or not enabled.")
            sys.exit(1)

    if not companies:
        print("No enabled companies found in config.ini. Run the web UI to add one.")
        sys.exit(1)

    for company in companies:
        cid     = company["companyID"]
        db_name = company["dbName"]

        print(f"\n{'='*50}")
        print(f"Company: {cid}  →  Database: {db_name}")
        print(f"{'='*50}")

        print(f"Ensuring database '{db_name}' exists...")
        try:
            ensure_database(cfg, db_name)
        except Exception as e:
            print(f"  ERROR: Could not create database: {e}")
            continue

        try:
            conn = get_connection(cfg, database=db_name)
        except Exception as e:
            print(f"  ERROR: Could not connect to {db_name}: {e}")
            continue

        print("Ensuring tables exist...")
        ensure_tables(conn)

        # ── RcsItems ──────────────────────────────────────────────────────────
        print(f"\n[{cid}] Fetching RcsItems...", flush=True)
        try:
            raw = fetch_rcs_items(rcs_base, company, start_date, end_date)
            print(f"  Received {len(raw)} records in date range", flush=True)
            if raw:
                records  = [normalize_rcs(r, cid) for r in raw]
                affected = upsert_rcs(conn, records)
                print(f"  Upserted {len(records)} records ({affected} rows affected)", flush=True)
            else:
                print("  No records in date range — skipping.", flush=True)
        except requests.HTTPError as e:
            print(f"  API ERROR: {e}", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)

        # ── Sales ─────────────────────────────────────────────────────────────
        print(f"\n[{cid}] Fetching Sales (paginated)...", flush=True)
        try:
            print(f"  Authenticating...", flush=True)
            token  = get_token(auth_base, company)
            int_id = company["companyIntId"]

            # Fetch first page to get total count / page count
            print(f"  Fetching page 1 to determine total...", flush=True)
            first       = fetch_sales_page(sales_base, token, int_id, 1, page_size)
            total_count = first["totalCount"]
            total_pages = math.ceil(total_count / page_size)
            print(f"  {total_count} total records across {total_pages} pages", flush=True)

            totals        = {"orders": 0, "items": 0, "payments": 0}
            pages_fetched = 0

            # Pages are sorted oldest-first. Work backwards from the last page
            # so we hit today's data first and stop as soon as we've passed
            # the start of the requested date range.
            for page in range(total_pages, 0, -1):
                print(f"  Fetching page {page}/{total_pages}...", flush=True)
                data   = first if page == 1 else fetch_sales_page(sales_base, token, int_id, page, page_size)
                orders = data["items"]
                pages_fetched += 1

                # Filter to orders within the requested date range
                in_range = [o for o in orders
                            if start_date <= (o.get("date") or "") <= end_date]

                if in_range:
                    counts = upsert_sales_page(conn, in_range)
                    totals["orders"]   += counts["orders"]
                    totals["items"]    += counts["items"]
                    totals["payments"] += counts["payments"]
                    print(f"    → {len(in_range)} orders matched — "
                          f"orders:{counts['orders']}  items:{counts['items']}  payments:{counts['payments']}",
                          flush=True)
                else:
                    print(f"    → 0 orders in range", flush=True)

                # If every order on this page predates start_date we can stop —
                # all earlier pages will be even older.
                page_dates = [o.get("date") or "" for o in orders if o.get("date")]
                if page_dates and max(page_dates) < start_date:
                    print(f"  Reached {start_date} boundary — stopping early.", flush=True)
                    break

            print(f"  Done — {pages_fetched} page(s) fetched, "
                  f"{totals['orders']} orders, {totals['items']} items, "
                  f"{totals['payments']} payments", flush=True)

        except requests.HTTPError as e:
            print(f"  API ERROR: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")

        # ── Customers ─────────────────────────────────────────────────────────
        print(f"\n[{cid}] Fetching Customers (paginated)...", flush=True)
        try:
            print(f"  Authenticating...", flush=True)
            token  = get_token(auth_base, company)
            int_id = company["companyIntId"]

            first_c      = fetch_customers_page(sales_base, token, int_id, 1, page_size)
            total_count  = first_c["totalCount"]
            total_pages  = math.ceil(total_count / page_size)
            print(f"  {total_count} total customers across {total_pages} pages", flush=True)

            totals = {"customers": 0, "groups": 0}
            for page in range(1, total_pages + 1):
                print(f"  Fetching page {page}/{total_pages}...", flush=True)
                data      = first_c if page == 1 else fetch_customers_page(sales_base, token, int_id, page, page_size)
                customers = data["items"]
                counts    = upsert_customers_page(conn, customers)
                totals["customers"] += counts["customers"]
                totals["groups"]    += counts["groups"]
                print(f"    → {len(customers)} customers, groups:{counts['groups']}", flush=True)

            print(f"  Done — {totals['customers']} customers, {totals['groups']} group memberships", flush=True)

        except requests.HTTPError as e:
            print(f"  API ERROR: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")

        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
