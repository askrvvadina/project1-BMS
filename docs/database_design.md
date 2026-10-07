# Business Management System — Database Design

## Overview

The BMS database stores information about users, employees, branches, products, inventory, sales, customers, shifts, expenses, notifications, and system activity.

## Tables

1. users
2. employees
3. branches
4. categories
5. products
6. branch_inventory
7. inventory_transactions
8. sales
9. sale_items
10. customers
11. shifts
12. expenses
13. notifications
14. audit_logs

## 1. users

Stores system accounts and role information.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique user identifier |
| username | String | Unique, Not Null | Username used to log in |
| email | String | Unique, Not Null | User email address |
| password_hash | String | Not Null | Hashed user password |
| role | String | Not Null | User role: Admin, Manager, or Employee |
| is_active | Boolean | Not Null | Indicates whether the account is active |
| created_at | DateTime | Not Null | Account creation date and time |

## 2. branches

Stores information about store branches.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique branch identifier |
| name | String | Not Null | Branch name |
| address | String | Not Null | Branch address |
| phone | String | | Branch phone number |
| status | String | Not Null | Branch status |

## 3. employees

Stores information about employees.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique employee identifier |
| full_name | String | Not Null | Employee full name |
| position | String | Not Null | Employee job position |
| phone | String | | Employee phone number |
| email | String | Unique | Employee email address |
| hire_date | Date | Not Null | Date when the employee was hired |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch where the employee works |
| status | String | Not Null | Employee status |

## 4. categories

Stores product categories.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique category identifier |
| name | String | Unique, Not Null | Category name |

## 5. products

Stores information about products.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique product identifier |
| name | String | Not Null | Product name |
| sku | String | Unique, Not Null | Unique product SKU |
| category_id | Integer | Foreign Key → categories.id, Not Null | Product category |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Legacy branch assignment retained during the inventory refactor |
| purchase_price | Decimal | Not Null | Product purchase price |
| selling_price | Decimal | Not Null | Product selling price |
| stock_quantity | Integer | Not Null | Legacy stock snapshot retained during the inventory refactor |
| minimum_stock | Integer | Not Null | Legacy minimum-stock snapshot retained during the inventory refactor |

## 6. branch_inventory

Stores each product's stock and minimum-stock threshold for a branch.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique branch inventory identifier |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch holding the product |
| product_id | Integer | Foreign Key → products.id, Not Null | Product in the branch |
| stock_quantity | Integer | Not Null, Default 0 | Current stock at this branch |
| minimum_stock | Integer | Not Null, Default 0 | Minimum desired stock at this branch |

The database enforces a unique `(branch_id, product_id)` pair. During this
compatibility phase, each product's current legacy branch and stock values are
copied into one branch inventory row; the legacy Product columns remain.
Existing inventory movements are associated with the row for the product's
current legacy branch. The old schema does not prove whether that branch was
also the product's branch when each historical movement occurred.
Active inventory management now reads and updates `branch_inventory`; the
legacy stock and minimum-stock values on `products` are retained for
compatibility and are not synchronized with later branch movements.

## 7. inventory_transactions

Stores the history of product stock movements.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique inventory transaction identifier |
| product_id | Integer | Foreign Key → products.id, Not Null | Product affected by the transaction |
| branch_inventory_id | Integer | Foreign Key → branch_inventory.id, Nullable | Branch inventory associated with the movement; nullable for compatibility |
| transaction_type | String | Not Null | Transaction type: stock_in or write_off |
| quantity | Integer | Not Null | Quantity of products moved |
| created_at | DateTime | Not Null | Date and time of the transaction |

## 8. customers

Stores customer information.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique customer identifier |
| name | String | Not Null | Customer name |
| phone | String | | Customer phone number |
| email | String | | Customer email address |
| registration_date | Date | Not Null | Customer registration date |    

## 9. sales

Stores completed sales.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique sale identifier |
| customer_id | Integer | Foreign Key → customers.id, Nullable | Customer associated with the sale |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch where the sale was made |
| total_amount | Decimal | Not Null | Final sale amount |
| discount | Decimal | Nullable | Optional discount applied to the sale |
| payment_method | String | Not Null | Payment method used for the sale |
| created_at | DateTime | Not Null | Date and time of the sale |

## 10. sale_items

Stores individual products included in a sale.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique sale item identifier |
| sale_id | Integer | Foreign Key → sales.id, Not Null | Sale containing this item |
| product_id | Integer | Foreign Key → products.id, Not Null | Product included in the sale |
| quantity | Integer | Not Null | Quantity sold |
| unit_price | Decimal | Not Null | Product selling price at the time of sale |

## 11. shifts

Stores employee work shifts.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique shift identifier |
| employee_id | Integer | Foreign Key → employees.id, Not Null | Employee assigned to the shift |
| date | Date | Not Null | Shift date |
| start_time | Time | Not Null | Shift start time |
| end_time | Time | Not Null | Shift end time |
| status | String | Not Null | Shift status: scheduled, completed, or cancelled |

## 12. expenses

Stores business expense records.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique expense identifier |
| branch_id | Integer | Foreign Key → branches.id, Not Null | Branch associated with the expense |
| category | String | Not Null | Expense category |
| amount | Decimal | Not Null | Expense amount |
| description | String | | Expense description |
| date | Date | Not Null | Expense date |

## 13. notifications

Stores important system notifications.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique notification identifier |
| user_id | Integer | Foreign Key → users.id, Not Null | User receiving the notification |
| message | String | Not Null | Notification message |
| is_read | Boolean | Not Null | Indicates whether the notification has been read |
| created_at | DateTime | Not Null | Notification creation date and time |

## 14. audit_logs

Stores important actions performed in the system.

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | Primary Key | Unique audit log identifier |
| user_id | Integer | Foreign Key → users.id, Not Null | User who performed the action |
| action | String | Not Null | Description of the performed action |
| object_type | String | Not Null | Type of object affected by the action |
| object_id | Integer | | Identifier of the affected object |
| created_at | DateTime | Not Null | Date and time when the action occurred |

# Database Relationships

## ER Diagram

users
  1
  ├──────────── N notifications
  │
  └──────────── N audit_logs


branches
  1
  ├──────────── N employees
  ├──────────── N products
  ├──────────── N branch_inventory
  ├──────────── N sales
  └──────────── N expenses


categories
  1
  └──────────── N products


employees
  1
  └──────────── N shifts


customers
  1
  └──────────── N sales


products
  1
  ├──────────── N branch_inventory
  ├──────────── N inventory_transactions
  │
  └──────────── N sale_items


branch_inventory
  1
  └──────────── N inventory_transactions


sales
  1
  └──────────── N sale_items