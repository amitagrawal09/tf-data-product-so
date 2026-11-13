-- =====================================================================================
-- Unity Catalog setup for the Sales Order data product (run once per metastore, as an admin)
-- Replace <lake> with the ADLS Gen2 account (Bicep output lakehouseStorageAccount) and
-- <access-connector-id> with the Azure Databricks Access Connector resource id.
-- =====================================================================================

-- Storage credential + external locations (Access Connector managed identity needs
-- Storage Blob Data Contributor on the lake account)
CREATE STORAGE CREDENTIAL IF NOT EXISTS sales_order_lake_mi
  WITH (AZURE_MANAGED_IDENTITY = (ACCESS_CONNECTOR_ID = '<access-connector-id>'))
  COMMENT 'Managed identity for the Sales Order lakehouse account';

CREATE EXTERNAL LOCATION IF NOT EXISTS sales_order_bronze
  URL 'abfss://bronze@<lake>.dfs.core.windows.net/'
  WITH (STORAGE CREDENTIAL sales_order_lake_mi)
  COMMENT 'Raw order status events written by the System API';

CREATE EXTERNAL LOCATION IF NOT EXISTS sales_order_silver
  URL 'abfss://silver@<lake>.dfs.core.windows.net/'
  WITH (STORAGE CREDENTIAL sales_order_lake_mi);

CREATE EXTERNAL LOCATION IF NOT EXISTS sales_order_gold
  URL 'abfss://gold@<lake>.dfs.core.windows.net/'
  WITH (STORAGE CREDENTIAL sales_order_lake_mi);

-- Catalog and medallion schemas
CREATE CATALOG IF NOT EXISTS sales_order
  MANAGED LOCATION 'abfss://silver@<lake>.dfs.core.windows.net/catalog'
  COMMENT 'Sales Order data product (owner: Sales & Customer Service domain)';

CREATE SCHEMA IF NOT EXISTS sales_order.bronze
  MANAGED LOCATION 'abfss://bronze@<lake>.dfs.core.windows.net/managed'
  COMMENT 'Raw orders and events, as received';
CREATE SCHEMA IF NOT EXISTS sales_order.silver
  MANAGED LOCATION 'abfss://silver@<lake>.dfs.core.windows.net/managed'
  COMMENT 'Sales Order data product, validated (contract v1)';
CREATE SCHEMA IF NOT EXISTS sales_order.gold
  MANAGED LOCATION 'abfss://gold@<lake>.dfs.core.windows.net/managed'
  COMMENT 'Order, customer and fulfillment marts';

-- Governance tags (surface in Purview via the Unity Catalog connector)
ALTER CATALOG sales_order SET TAGS ('data_product' = 'sales_order', 'domain' = 'sales_and_customer_service', 'contract_version' = 'v1');
ALTER SCHEMA sales_order.bronze SET TAGS ('medallion' = 'bronze', 'contains_pii' = 'false');
ALTER SCHEMA sales_order.silver SET TAGS ('medallion' = 'silver', 'contains_pii' = 'contact_names');
ALTER SCHEMA sales_order.gold   SET TAGS ('medallion' = 'gold',   'contains_pii' = 'false');

-- Groups (create in the account console / SCIM from Entra ID first)
--   sales-order-product-owners   : own and evolve the contract
--   sales-order-engineers        : run the pipeline
--   sales-order-consumers        : read silver + gold (customer service, planning, finance, analytics)
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG sales_order TO `sales-order-product-owners`;
GRANT ALL PRIVILEGES ON SCHEMA sales_order.bronze TO `sales-order-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA sales_order.silver TO `sales-order-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA sales_order.gold   TO `sales-order-engineers`;

GRANT USE CATALOG ON CATALOG sales_order TO `sales-order-consumers`;
GRANT USE SCHEMA, SELECT ON SCHEMA sales_order.silver TO `sales-order-consumers`;
GRANT USE SCHEMA, SELECT ON SCHEMA sales_order.gold   TO `sales-order-consumers`;
GRANT READ FILES ON EXTERNAL LOCATION sales_order_bronze TO `sales-order-engineers`;

-- Delta Sharing for external consumers (output port "Analytics tables")
CREATE SHARE IF NOT EXISTS sales_order_status COMMENT 'Order status marts for trading partners';
-- ALTER SHARE sales_order_status ADD TABLE sales_order.gold.gold_order_status_daily;
